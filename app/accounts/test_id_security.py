"""P1 보안 검토 반영: S2 대소문자 무시 유일(DB)·예약 아이디, S4 시간 차, S5 손님 잇기는 처음 PC만, S6 임시 비밀번호 뒤 강제 바꾸기·🔔, Q7 깨진 구 해시."""
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.core.cache import caches
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings

from collect.models import Notification
from spaces.models import Member, School, Space

from .hashers import G5PBKDF2PasswordHasher
from .models import DEVICE_COOKIE, DeviceToken, PasswordChangeRequired
from .test_id_login import FAST, LEGACY, PW, IdSchoolBase, login, signup
from .tests import GOOGLE, TOKEN

User = get_user_model()
GUEST_LINK = "처음 들어온 PC에서 이어 주세요"


@FAST
class UniqueIdTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()

    def test_db_refuses_case_only_duplicate(self):
        User.objects.create(username="CaseX")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(username="casex")

    def test_race_past_app_check_still_says_taken(self):
        User.objects.create(username="SameId")
        with mock.patch("accounts.views._id_taken", return_value=False):
            r = signup(self.client, "sameid")
        self.assertContains(r, "이미 쓰는 아이디", status_code=400)
        self.assertEqual(User.objects.filter(username__iexact="sameid").count(), 1)

    def test_reserved_ids_file(self):
        fd, path = tempfile.mkstemp()
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "w") as f:
            f.write("OldOne\n\n  legacy_2  \n")
        with override_settings(RESERVED_IDS_FILE=path):
            for name in ("oldone", "LEGACY_2"):
                self.assertContains(signup(Client(), name), "이미 쓰는 아이디", status_code=400)
            self.assertEqual(signup(Client(), "fresh_one").status_code, 302)
        with override_settings(RESERVED_IDS_FILE=path + ".missing"):
            self.assertEqual(signup(Client(), "oldone").status_code, 302)  # 파일이 없으면 끔


@FAST
class TimingAndBrokenHashTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()

    def test_broken_legacy_hash_is_wrong_password_not_500(self):
        for i, bad in enumerate(("g5pbkdf2$sha256:12000:abc", "g5pbkdf2$sha1:12000:a:b", "g5pbkdf2$sha256:x:a:b")):
            User.objects.create(username=f"broken{i}", password=bad)
            with self.assertLogs("accounts.hashers", "WARNING") as logs:
                self.assertEqual(login(Client(), f"broken{i}", "test-pass-1234").status_code, 400, bad)
            self.assertNotIn("test-pass-1234", "".join(logs.output))
        User.objects.create(username="nohasher", password="sha1:12000:a:b")  # 해셔를 못 찾는 형식
        self.assertEqual(login(Client(), "nohasher", "test-pass-1234").status_code, 400)

    def test_legacy_wrong_and_inactive_pay_default_hash_cost(self):
        User.objects.create(username="legacy1", password=LEGACY)
        off = User.objects.create(username="offline", is_active=False)
        off.set_password(PW)
        off.save()
        for name, pw in (("legacy1", "wrong-pass-0"), ("offline", PW)):
            with mock.patch("accounts.views.make_password") as mp:
                self.assertEqual(login(Client(), name, pw).status_code, 400)
            mp.assert_called_once()

    def test_harden_runtime_fills_iterations_up_to_default(self):
        with mock.patch("accounts.hashers.hashlib.pbkdf2_hmac") as pb:
            G5PBKDF2PasswordHasher().harden_runtime("x", LEGACY)
        self.assertEqual(pb.call_args.args[3], PBKDF2PasswordHasher.iterations - 12000)


@FAST
class GuestLinkTests(TestCase):
    """S5: 손님을 계정으로 잇는 것은 그 손님의 첫 기기 토큰을 가진 PC(또는 처음 들어온 세션)에서만."""

    def setUp(self):
        caches["ratelimit"].clear()
        school = School.objects.create(neis_atpt="J10", neis_code="9", name="온누리초")
        self.space = Space.objects.create(school=school, grade=3, name="온누리초 3학년", invite_token="inv-onnuri-1234567890")
        self.guest = User.objects.create(username="guest:orig", first_name="손님")
        self.seat = Member.objects.create(space=self.space, user=self.guest, name="손님", cls="3-1")
        self.first = DeviceToken.issue(self.guest, origin=True)  # 처음 PC(손님을 만든 세션에서 받은 토큰)
        self.later = DeviceToken.issue(self.guest)  # 같은 반·이름으로 들어온 다른 PC
        self.acct = User.objects.create(username="haveid")
        self.acct.set_password(PW)
        self.acct.save()

    def pc(self, raw):
        c = Client()
        c.cookies[DEVICE_COOKIE] = raw
        return c

    def test_other_pc_cannot_link_by_signup_login_or_google(self):
        c = self.pc(self.later)
        self.assertContains(signup(c, "taker"), GUEST_LINK, status_code=403)
        self.assertContains(login(c, "haveid", PW), GUEST_LINK, status_code=403)
        with override_settings(**GOOGLE):
            c.get("/auth/google/login")
            info = {"sub": "taker", "email": "", "name": "가로채기"}
            with mock.patch("accounts.views._http_json", side_effect=[TOKEN, info]):
                r = c.get("/auth/google/callback", {"state": c.session["google_state"], "code": "x"})
        self.assertContains(r, GUEST_LINK, status_code=403)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.user.username, "guest:orig")
        self.assertFalse(User.objects.filter(username__in=["taker", "google:taker"]).exists())

    def test_first_pc_can_link(self):
        r = signup(self.pc(self.first), "mine_now")
        self.assertEqual(r.status_code, 302)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.user.username, "mine_now")

    def test_attack_same_name_with_remember_never_gets_origin_token(self):
        """재검토 S5 시나리오: 처음 PC는 기억 끔(토큰 없음). 초대 링크를 가진 남이 같은 반·이름 + 기억 켬으로 들어와
        그 손님의 '첫' 토큰을 받는다. 그 토큰에는 처음 PC 표시가 없어 아이디 가입·로그인·구글로 자리를 가져가지 못한다."""
        owner = Client()
        owner.get(f"/j/{self.space.invite_token}")
        self.assertEqual(owner.post("/join", {"cls": "3-2", "name": "박교사", "remember": "0"}).status_code, 302)
        seat = Member.objects.get(space=self.space, name="박교사")
        self.assertFalse(DeviceToken.objects.filter(user=seat.user).exists())
        taker = Client()
        taker.get(f"/j/{self.space.invite_token}")
        r = taker.post("/join", {"cls": "3-2", "name": "박교사", "remember": ["0", "1"]})
        self.assertEqual(r.status_code, 302)
        tok = DeviceToken.objects.get(user=seat.user)  # 이 손님의 첫(유일한) 토큰
        self.assertFalse(tok.origin)
        self.assertContains(signup(taker, "taker2"), GUEST_LINK, status_code=403)
        self.assertContains(login(taker, "haveid", PW), GUEST_LINK, status_code=403)
        again = Client()  # 브라우저를 닫고 기기 쿠키로 다시 들어와 시도
        again.cookies[DEVICE_COOKIE] = r.cookies[DEVICE_COOKIE].value
        self.assertContains(signup(again, "taker3"), GUEST_LINK, status_code=403)
        seat.refresh_from_db()
        self.assertTrue(seat.user.username.startswith("guest:"))
        self.assertEqual(signup(owner, "owner_id").status_code, 302)  # 주인(처음 세션)은 잇는다
        seat.refresh_from_db()
        self.assertEqual(seat.user.username, "owner_id")

    def test_origin_session_remember_gives_origin_token(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        r = c.post("/join", {"cls": "3-3", "name": "기억샘", "remember": ["0", "1"]})
        tok = DeviceToken.objects.get(user=Member.objects.get(space=self.space, name="기억샘").user)
        self.assertTrue(tok.origin)
        later = Client()  # 세션이 끝난 뒤 같은 PC(쿠키)
        later.cookies[DEVICE_COOKIE] = r.cookies[DEVICE_COOKIE].value
        self.assertEqual(signup(later, "remember_id").status_code, 302)

    def test_session_that_created_the_guest_can_link(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        r = c.post("/join", {"cls": "3-2", "name": "새손님"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(login(c, "haveid", PW).status_code, 302)
        self.assertTrue(Member.objects.filter(space=self.space, user=self.acct, name="새손님").exists())


@FAST
class ForcedChangeTests(IdSchoolBase):
    """S6: 임시 비밀번호로 들어오면 바꾸기 전에는 바꾸기 화면만. 발급 사실은 당사자 🔔."""

    def issue(self):
        _, seat = self.enter_by_signup()
        r = self.manager.post(f"{self.base}/school/members/{seat.key}/password", headers={"HX-Request": "true"})
        return seat, r.context["temp"]

    def test_bell_to_the_member(self):
        seat, temp = self.issue()
        n = Notification.objects.in_space(self.hub).get(member=seat)
        self.assertIn("임시 비밀번호", n.text)
        self.assertNotIn(temp, n.text)

    def test_must_change_before_anything_else(self):
        seat, temp = self.issue()
        self.assertTrue(PasswordChangeRequired.objects.filter(user=seat.user).exists())
        c = Client()
        self.assertEqual(login(c, "newid", temp, next=f"{self.base}/school").status_code, 302)
        r = c.get(f"{self.base}/school")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/account/password"))
        self.assertEqual(c.post(f"{self.base}/settings/me", {"name": "x"}).status_code, 302)  # 쓰기도 막힘
        self.assertContains(c.get(r["Location"]), "새 비밀번호")
        self.assertContains(c.post("/account/password", {"password": "short", "password2": "short"}), "8자 이상", status_code=400)
        new = "brand-new-pass-5"
        r = c.post("/account/password", {"password": new, "password2": new, "next": f"{self.base}/school"})
        self.assertRedirects(r, f"{self.base}/school", fetch_redirect_response=False)
        self.assertEqual(c.get(f"{self.base}/school").status_code, 200)
        self.assertFalse(PasswordChangeRequired.objects.filter(user=seat.user).exists())
        self.assertEqual(login(Client(), "newid", new).status_code, 302)
        self.assertEqual(login(Client(), "newid", temp).status_code, 400)

    def test_page_only_for_flagged_member(self):
        c = Client()
        c.force_login(self.u5)
        self.assertEqual(c.get("/account/password").status_code, 302)
