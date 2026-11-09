"""P1 아이디 로그인·가입: 구버전 해시, 대소문자 무시, 가입 → 학교 입장 → 관리자, 시도 제한, 비밀번호 바꾸기, 임시 비밀번호."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings

from spaces.models import Member, MemberLog, School, Space
from spaces.test_school import JoinBase

from .models import DEVICE_COOKIE, GUEST_ORIGIN, DeviceToken, hash_token
from .views import LOGIN_FAILS_ID, LOGIN_FAILS_ID_ALL, LOGIN_FAILS_IP, LOGIN_LOCKED, LOGIN_WRONG, SIGNUPS_PER_HOUR_IP

User = get_user_model()
# 구버전(그누보드) 표본: 비밀번호 test-pass-1234 (가짜)
LEGACY = "g5pbkdf2$sha256:12000:+PwiegD44uYZ/wgMyWAzMh6h4fRh54KJ:4fQuIlivgzCzzsEiPP6vYtEGcNc6DcsS"
FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher", "accounts.hashers.G5PBKDF2PasswordHasher"])
PW = "gt-strong-pass-77"


def login(c, username, password, **extra):
    return c.post("/auth/login", {"username": username, "password": password, **extra})


def signup(c, username, password=PW, name="이아이디", password2=None, **extra):
    return c.post("/signup", {"username": username, "password": password, "password2": password2 or password, "name": name, **extra})


class LegacyHashTests(TestCase):
    """기본 해셔 그대로(실제 PBKDF2)로 구버전 해시 → 새 해시 교체를 본다."""

    def setUp(self):
        caches["ratelimit"].clear()
        self.user = User.objects.create(username="OldTeacher", first_name="옛선생", password=LEGACY)

    def test_legacy_hash_logs_in_and_is_replaced(self):
        r = login(self.client, "OldTeacher", "test-pass-1234")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        self.user.refresh_from_db()
        self.assertTrue(self.user.password.startswith("pbkdf2_sha256$"))  # 첫 로그인 때 기본 해셔로
        self.assertTrue(login(Client(), "OldTeacher", "test-pass-1234").status_code == 302)  # 새 해시로도 그대로

    def test_legacy_wrong_password_refused_and_hash_kept(self):
        r = login(self.client, "OldTeacher", "test-pass-12345")
        self.assertContains(r, LOGIN_WRONG, status_code=400)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, LEGACY)


@FAST
class IdLoginTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.user = User.objects.create(username="TeachKim", first_name="김아이디")
        self.user.set_password(PW)
        self.user.save()

    def test_case_insensitive_login_and_next(self):
        r = login(self.client, "teachkim", PW, next="/forms")
        self.assertRedirects(r, "/forms", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_failure_does_not_reveal_whether_id_exists(self):
        a = login(Client(), "TeachKim", "wrong-password-1")
        b = login(Client(), "nobody_here", "wrong-password-1")
        self.assertEqual((a.status_code, b.status_code), (400, 400))
        self.assertContains(a, LOGIN_WRONG, status_code=400)
        self.assertContains(b, LOGIN_WRONG, status_code=400)

    def test_google_and_guest_usernames_cannot_log_in_by_id(self):
        for name in ("google:1", "guest:abc"):
            u = User.objects.create(username=name)
            u.set_password(PW)
            u.save()
            self.assertEqual(login(Client(), name, PW).status_code, 400)

    def test_remember_issues_device_token_like_google(self):
        r = login(self.client, "TeachKim", PW, remember=["0", "1"])
        self.assertIn(DEVICE_COOKIE, r.cookies)
        self.assertEqual(DeviceToken.objects.filter(user=self.user).count(), 1)
        c = Client()
        r = login(c, "TeachKim", PW, remember="0")
        self.assertNotIn(DEVICE_COOKIE, r.cookies)
        self.assertTrue(c.session.get_expire_at_browser_close())

    def test_google_button_in_same_form_goes_to_google_with_next_and_remember(self):
        r = self.client.post("/auth/login", {"via": "google", "next": "/forms", "remember": ["0", "1"], "username": "", "password": ""})
        self.assertEqual(r["Location"], "/auth/google/login?next=%2Fforms&remember=1")

    def test_logged_in_member_root_goes_to_school_search(self):
        login(self.client, "TeachKim", PW)
        self.assertTemplateUsed(self.client.get("/"), "spaces/start_school.html")  # 공간 없음 → 학교 찾기(구글과 같음)

    def test_logout_all_keeps_password(self):
        login(self.client, "TeachKim", PW, remember=["0", "1"])
        other = Client()
        login(other, "TeachKim", PW)
        school = School.objects.create(neis_atpt="J10", neis_code="1", name="가람초")
        space = Space.objects.create(school=school, grade=4, name="가람초 4학년")
        Member.objects.create(space=space, user=self.user, name="김아이디", cls="4-1")
        self.client.post(f"/s/{space.token}/settings/logout-all")
        self.assertFalse(DeviceToken.objects.filter(user=self.user).exists())
        self.assertEqual(other.get(f"/s/{space.token}/settings").status_code, 302)  # 다른 PC 세션도 끊김
        self.assertEqual(login(Client(), "TeachKim", PW).status_code, 302)  # 비밀번호는 그대로


@FAST
class RateLimitTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.user = User.objects.create(username="limited")
        self.user.set_password(PW)
        self.user.save()

    def test_failures_per_id_and_ip_lock_that_pair_only(self):
        """S1: 잠금은 (아이디+IP). 같은 IP에서 10번 틀리면 그 IP만 막히고, 다른 IP의 본인은 그대로 들어온다."""
        for _ in range(LOGIN_FAILS_ID):
            login(self.client, "LIMITED", "wrong-wrong-1")
        self.assertContains(login(self.client, "limited", PW), LOGIN_LOCKED, status_code=429)
        self.assertEqual(login(Client(REMOTE_ADDR="10.9.9.9"), "limited", PW).status_code, 302)

    def test_failures_per_id_overall_from_many_ips(self):
        """S1: 아이디 전체 한도는 따로, 훨씬 높게(여러 IP로 나눠 때리는 대입)."""
        for i in range(LOGIN_FAILS_ID_ALL):
            login(Client(REMOTE_ADDR=f"10.{i // 250}.{i % 250}.1"), "limited", "wrong-wrong-1")
        self.assertContains(login(Client(REMOTE_ADDR="10.200.0.1"), "limited", PW), LOGIN_LOCKED, status_code=429)

    def test_cache_down_login_and_signup_limits_count_in_process(self):
        """S3: ratelimit 캐시가 죽으면 로그인·가입 제한만 프로세스 메모리로 센다."""
        from config.ratelimit import _LOCAL

        _LOCAL.clear()
        self.addCleanup(_LOCAL.clear)
        class Down:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise ConnectionError("down")
                return boom

        with mock.patch("config.ratelimit.caches", {"ratelimit": Down()}):
            for _ in range(LOGIN_FAILS_ID):
                login(self.client, "limited", "wrong-wrong-1")
            self.assertEqual(login(self.client, "limited", PW).status_code, 429)
            from config.ratelimit import allow
            self.assertTrue(all(allow("other", "x", 1, 60) for _ in range(5)))  # 다른 제한은 그대로 막지 않는다

    def test_failures_per_ip(self):
        for i in range(LOGIN_FAILS_IP):
            login(self.client, f"someone{i}", "wrong-wrong-1")
        self.assertContains(login(self.client, "limited", PW), LOGIN_LOCKED, status_code=429)
        self.assertEqual(login(Client(REMOTE_ADDR="10.1.1.1"), "limited", PW).status_code, 302)  # 다른 IP는 그대로

    def test_success_is_not_counted(self):
        for _ in range(LOGIN_FAILS_ID + 2):
            self.assertEqual(login(Client(), "limited", PW).status_code, 302)

    def test_signup_posts_count_even_when_refused(self):
        """S1: 가입 POST는 결과와 상관없이 IP당 센다(아이디 있나 무제한 확인 막기)."""
        User.objects.create(username="taken")
        for _ in range(SIGNUPS_PER_HOUR_IP):
            self.assertContains(signup(self.client, "taken"), "이미 쓰는 아이디", status_code=400)
        self.assertEqual(signup(self.client, "free_one").status_code, 429)
        self.assertFalse(User.objects.filter(username="free_one").exists())

    def test_signups_per_ip(self):
        for i in range(SIGNUPS_PER_HOUR_IP):
            self.assertEqual(signup(Client(), f"new_{i}").status_code, 302)
        r = signup(Client(), "one_more")
        self.assertEqual(r.status_code, 429)
        self.assertFalse(User.objects.filter(username="one_more").exists())


@FAST
class SignupTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()

    def test_signup_logs_in_and_follows_next(self):
        r = signup(self.client, "Hong_Gil1", name="홍길동", next="/forms")
        self.assertRedirects(r, "/forms", fetch_redirect_response=False)
        u = User.objects.get(username="Hong_Gil1")
        self.assertEqual(u.first_name, "홍길동")
        self.assertTrue(u.check_password(PW))
        self.assertEqual(self.client.session["_auth_user_id"], str(u.pk))

    def test_duplicate_id_with_other_case_refused(self):
        signup(Client(), "SameId")
        r = signup(Client(), "sameid")
        self.assertContains(r, "이미 쓰는 아이디", status_code=400)
        self.assertEqual(User.objects.filter(username__iexact="sameid").count(), 1)

    def test_bad_inputs(self):
        cases = [
            ({"username": "ab"}, "영문·숫자·_ 3~20자"),
            ({"username": "a:b_c"}, "영문·숫자·_ 3~20자"),
            ({"username": "한글아이디"}, "영문·숫자·_ 3~20자"),
            ({"username": "x" * 21}, "영문·숫자·_ 3~20자"),
            ({"username": "okname", "password": "short1"}, "8자 이상"),
            ({"username": "okname", "password": "password"}, "8자 이상"),  # 흔한 비밀번호
            ({"username": "okname", "password2": "different-pass-9"}, "서로 달라요"),
            ({"username": "okname", "name": "   "}, "이름을 써 주세요"),
        ]
        for data, msg in cases:
            with self.subTest(data):
                r = signup(Client(), **data)
                self.assertContains(r, msg, status_code=400)
        self.assertFalse(User.objects.exclude(username__startswith="x").filter(username__iexact="okname").exists())

    def test_signup_page_has_terms_and_privacy(self):
        r = self.client.get("/signup")
        self.assertContains(r, 'href="/terms"')
        self.assertContains(r, 'href="/privacy"')

    def test_guest_signup_keeps_same_user_and_seat(self):
        school = School.objects.create(neis_atpt="J10", neis_code="2", name="나래초")
        space = Space.objects.create(school=school, grade=3, name="나래초 3학년")
        guest = User.objects.create(username="guest:abc", first_name="손님")
        seat = Member.objects.create(space=space, user=guest, name="손님", cls="3-1")
        self.client.cookies[DEVICE_COOKIE] = DeviceToken.issue(guest, origin=True)  # 이 PC가 처음 PC
        other = DeviceToken.issue(guest)  # 같은 반·이름으로 다른 PC에서 받은 토큰
        self.client.force_login(guest)
        r = signup(self.client, "guest_now", name="손님")
        self.assertEqual(r.status_code, 302)
        guest.refresh_from_db()
        seat.refresh_from_db()
        self.assertEqual((guest.username, seat.user_id), ("guest_now", guest.pk))
        self.assertFalse(DeviceToken.objects.filter(token_hash=hash_token(other)).exists())  # 다른 PC 토큰은 끊김

    def test_guest_login_to_existing_id_absorbs_seat(self):
        school = School.objects.create(neis_atpt="J10", neis_code="3", name="다솜초")
        space = Space.objects.create(school=school, grade=2, name="다솜초 2학년")
        acct = User.objects.create(username="haveid")
        acct.set_password(PW)
        acct.save()
        guest = User.objects.create(username="guest:xyz", first_name="손님")
        Member.objects.create(space=space, user=guest, name="손님", cls="2-1")
        self.client.force_login(guest)
        session = self.client.session
        session[GUEST_ORIGIN] = guest.pk  # 이 세션이 손님을 만들었다
        session.save()
        self.assertEqual(login(self.client, "haveid", PW).status_code, 302)
        self.assertTrue(Member.objects.filter(space=space, user=acct).exists())
        self.assertFalse(User.objects.filter(pk=guest.pk).exists())


class IdSchoolBase(JoinBase):
    """학교 공간 + 아이디 가입으로 들어오기(테스트 없음, 기반만)."""

    def setUp(self):
        super().setUp()
        self.manager = Client()
        self.manager.force_login(self.u4)
        self.base = f"/s/{self.hub.token}"

    def enter_by_signup(self, username="newid", name="아이디샘"):
        c = Client()
        c.get(self.link)
        page = c.get("/join")
        self.assertContains(page, 'action="/auth/login"')
        r = signup(c, username, name=name, next="/join")
        self.assertRedirects(r, "/join", fetch_redirect_response=False)
        self.assertContains(c.get("/join"), name)  # 가입 이름을 미리 채운다
        r = c.post("/join", {"group": "office", "role": "교무", "name": name})
        self.assertRedirects(r, f"{self.base}/welcome")
        return c, Member.objects.in_space(self.hub).get(user__username=username)


@FAST
class SchoolFlowTests(IdSchoolBase):
    """가입 → 학교 공간 입장 → 관리자 지정까지 구글 회원과 같게. 임시 비밀번호."""

    def test_signup_join_then_manager(self):
        c, seat = self.enter_by_signup()
        self.assertContains(c.get(f"{self.base}/welcome"), "아이디 newid로 이어 뒀어요")
        r = self.manager.post(f"{self.base}/school/members/{seat.key}/manager", {"on": "1"})
        self.assertEqual(r.status_code, 302)
        seat.refresh_from_db()
        self.assertTrue(seat.is_manager)

    def test_existing_id_login_from_invite_keeps_invite(self):
        u = User.objects.create(username="ExistId", first_name="있던샘")
        u.set_password(PW)
        u.save()
        c = Client()
        c.get(self.link)
        r = login(c, "existid", PW, next="/join")
        self.assertRedirects(r, "/join", fetch_redirect_response=False)
        self.assertContains(c.get("/join"), 'name="group"')

    def test_temp_password_once_and_cuts_other_devices(self):
        c, seat = self.enter_by_signup()
        other = Client()
        login(other, "newid", PW, remember=["0", "1"])
        self.assertTrue(DeviceToken.objects.filter(user=seat.user).exists())
        r = self.manager.post(f"{self.base}/school/members/{seat.key}/password", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        temp = r.context["temp"]
        self.assertContains(r, temp)
        self.assertEqual(len(temp), 10)
        self.assertFalse(DeviceToken.objects.filter(user=seat.user).exists())
        self.assertEqual(c.get(f"{self.base}/settings").status_code, 302)  # 그 회원의 세션 끊김
        self.assertEqual(login(Client(), "newid", PW).status_code, 400)
        self.assertEqual(login(Client(), "newid", temp).status_code, 302)
        log = MemberLog.objects.in_space(self.hub).latest("pk")
        self.assertEqual((log.by, log.name, log.after), ("송민재", "아이디샘", "임시 비밀번호 발급"))
        self.assertNotIn(temp, f"{log.before}{log.after}")
        self.assertNotContains(self.manager.get(f"{self.base}/school"), temp)  # 다시 볼 수 없다

    def test_temp_password_refused_for_manager_google_other_school_and_non_manager(self):
        _, seat = self.enter_by_signup()
        Member.objects.filter(pk=seat.pk).update(is_manager=True)
        r = self.manager.post(f"{self.base}/school/members/{seat.key}/password", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 400)  # 관리자 대상 불가
        google_seat = Member.objects.in_space(self.hub).get(user=self.u5)
        r = self.manager.post(f"{self.base}/school/members/{google_seat.key}/password", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 400)  # 구글 회원
        r = self.manager.post(f"{self.base}/school/members/{self.far_member.key}/password")
        self.assertEqual(r.status_code, 404)  # 다른 학교
        teacher = Client()
        teacher.force_login(self.u5)
        r = teacher.post(f"{self.base}/school/members/{seat.key}/password")
        self.assertEqual(r.status_code, 403)  # 관리자 아님
        self.assertEqual(login(Client(), "newid", PW).status_code, 302)  # 비밀번호 그대로

    def test_roster_shows_id_member_and_temp_button_only_for_id_members(self):
        _, seat = self.enter_by_signup()
        page = self.manager.get(f"{self.base}/school").content.decode()
        self.assertIn(f"/school/members/{seat.key}/password", page)
        self.assertContains(self.manager.get(f"{self.base}/settings?a=space"), "아이디 가입")
        google_seat = Member.objects.in_space(self.hub).get(user=self.u5)
        self.assertNotIn(f"/school/members/{google_seat.key}/password", page)


@FAST
class ChangePasswordTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        school = School.objects.create(neis_atpt="J10", neis_code="4", name="한별초")
        self.space = Space.objects.create(school=school, grade=4, name="한별초 4학년")
        self.user = User.objects.create(username="changer", first_name="바꿈")
        self.user.set_password(PW)
        self.user.save()
        Member.objects.create(space=self.space, user=self.user, name="바꿈", cls="4-1")
        self.url = f"/s/{self.space.token}/settings/password"

    def test_only_id_member_sees_and_can_change(self):
        login(self.client, "changer", PW)
        self.assertContains(self.client.get(f"/s/{self.space.token}/settings"), "비밀번호 바꾸기")
        g = User.objects.create(username="google:77", first_name="구글")
        Member.objects.create(space=self.space, user=g, name="구글", cls="4-2")
        c = Client()
        c.force_login(g)
        self.assertNotContains(c.get(f"/s/{self.space.token}/settings"), "비밀번호 바꾸기")
        self.assertEqual(c.post(self.url, {"current": "", "password": PW, "password2": PW}).status_code, 403)

    def test_wrong_current_refused(self):
        login(self.client, "changer", PW)
        r = self.client.post(self.url, {"current": "nope-nope-1", "password": "new-pass-word-9", "password2": "new-pass-word-9"},
                             headers={"HX-Request": "true"})
        self.assertContains(r, "지금 비밀번호가 맞지 않아요", status_code=400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PW))

    def test_change_keeps_this_pc_and_cuts_others(self):
        login(self.client, "changer", PW, remember=["0", "1"])
        other = Client()
        login(other, "changer", PW, remember=["0", "1"])
        self.assertEqual(DeviceToken.objects.filter(user=self.user).count(), 2)
        new = "new-pass-word-9"
        r = self.client.post(self.url, {"current": PW, "password": new, "password2": new}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"/s/{self.space.token}/settings").status_code, 200)  # 이 PC 로그인 유지
        self.assertEqual(DeviceToken.objects.filter(user=self.user).count(), 1)
        self.assertEqual(other.get(f"/s/{self.space.token}/settings").status_code, 302)  # 다른 PC: 세션·기기 토큰 끊김
        self.assertEqual(login(Client(), "changer", new).status_code, 302)
