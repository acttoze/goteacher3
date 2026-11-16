"""V1 입장권 로그인: 구 고티처가 만든 입장권으로 새 고티처에 바로 들어온다(이관 계정만)."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings

from legacy.models import LegacyMap
from spaces.models import Member, School, Space

from .models import DEVICE_COOKIE, DeviceToken
from .views import LOGIN_FAILS_ID, _count_fail

User = get_user_model()
KEY = "test-legacy-key-가나다-0123456789abcdef"  # 32바이트 이상
MB_NO = 4321
# 다시 쓰기 막기는 Redis일 때만 켜진다. 테스트는 프로세스 메모리 캐시를 Redis 자리에 둔다.
REDIS_STANDIN = mock.patch("accounts.views._replay_cache", new=lambda: caches["ratelimit"])


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ticket(u="OldKim", m=MB_NO, exp=None, n=None, key=KEY, payload=None):
    """PHP 쪽(troad_preview_ticket)과 같은 방식으로 만든다."""
    body = payload if payload is not None else {"u": u, "m": m, "exp": int(time.time()) + 60 if exp is None else exp,
                                                "n": n or secrets.token_hex(16)}
    p = b64(json.dumps(body).encode())
    return p + "." + b64(hmac.new(key.encode(), p.encode(), hashlib.sha256).digest())


@REDIS_STANDIN
@override_settings(LEGACY_SSO_KEY=KEY)
class LegacySsoTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.user = User.objects.create(username="OldKim", first_name="김옛날")
        LegacyMap.objects.create(kind="user", legacy_id=str(MB_NO), obj_id=self.user.pk)
        school = School.objects.create(neis_atpt="E10", neis_code="1234567", name="새솔초")
        self.space = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name="새솔초")
        Member.objects.create(space=self.space, user=self.user, name="김옛날")
        self.landing = f"/s/{self.space.token}/today"

    def go(self, t, c=None):
        return (c or self.client).get("/auth/legacy", {"t": t})

    def assert_in(self, r, c=None, user=None):
        self.assertRedirects(r, self.landing, fetch_redirect_response=False)
        self.assertEqual((c or self.client).session["_auth_user_id"], str((user or self.user).pk))

    def assert_refused(self, r, c=None):
        self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", (c or self.client).session)

    def test_valid_ticket_logs_in_and_lands_in_school_space(self):
        self.assert_in(self.go(ticket()))
        self.assertTrue(self.client.session.get_expire_at_browser_close())  # 'PC 기억' 기본 끔과 같다
        self.assertFalse(DeviceToken.objects.filter(user=self.user).exists())

    def test_username_case_insensitive_and_mb_no_as_string(self):
        self.assert_in(self.go(ticket(u="oldkim", m=str(MB_NO))))

    def test_no_school_space_goes_where_login_usually_goes(self):
        Member.objects.filter(user=self.user).delete()
        self.assertRedirects(self.go(ticket()), "/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_forged_signature_refused(self):
        self.assert_refused(self.go(ticket(key="other-key-0123456789abcdef0123456789")))
        p, s = ticket().split(".")
        self.assert_refused(self.go(p + "." + s[:-2] + ("AA" if s[-2:] != "AA" else "BB")))
        other = b64(json.dumps({"u": "admin", "m": 1, "exp": int(time.time()) + 60, "n": secrets.token_hex(16)}).encode())
        self.assert_refused(self.go(other + "." + s))  # 서명은 두고 내용만 바꿔 치기

    def test_malformed_refused(self):
        for t in ("", "abc", "a.b.c", "....", "가나.다라", "x" * 3000):
            self.assert_refused(self.go(t))
        self.assert_refused(self.client.get("/auth/legacy"))
        exp, n = int(time.time()) + 60, secrets.token_hex(16)
        for body in ({"u": "OldKim", "m": MB_NO, "exp": "9999999999", "n": n},
                     {"u": "OldKim", "m": MB_NO, "exp": exp, "n": "short"},
                     {"u": ["OldKim"], "m": MB_NO, "exp": exp, "n": n},
                     {"u": "OldKim", "m": MB_NO, "exp": True, "n": n},
                     {"u": "OldKim", "exp": exp, "n": n},  # m 없음(아이디만으로는 찾지 않는다)
                     {"u": "OldKim", "m": "43a", "exp": exp, "n": n},
                     {"u": "OldKim", "m": -1, "exp": exp, "n": n},
                     {"u": "OldKim", "m": True, "exp": exp, "n": n},
                     {"u": "OldKim", "m": 4321.0, "exp": exp, "n": n},
                     ["OldKim"]):
            self.assert_refused(self.go(ticket(payload=body)))

    def test_expired_and_too_far_future_refused(self):
        now = int(time.time())
        self.assert_refused(self.go(ticket(exp=now - 1)))
        self.assert_refused(self.go(ticket(exp=now + 126)))
        self.assert_in(self.go(ticket(exp=now + 110)))

    def test_reuse_refused(self):
        t = ticket()
        first = Client()
        self.assert_in(self.go(t, first), first)
        c = Client()
        self.assert_refused(self.go(t, c), c)

    def test_only_migrated_accounts(self):
        """아이디가 같아도 이관 기록(LegacyMap user)이 없거나 다른 계정을 가리키면 거부. 새 계정은 만들지 않는다."""
        self.assert_refused(self.go(ticket(m=MB_NO + 1)))  # 이관 기록 없음
        plain = User.objects.create(username="PlainPark")  # 새 고티처에서 가입한 같은 이름 회원
        self.assert_refused(self.go(ticket(u="PlainPark")))  # m은 OldKim 것
        LegacyMap.objects.create(kind="member", legacy_id="77", obj_id=plain.pk)  # 다른 종류 기록
        self.assert_refused(self.go(ticket(u="PlainPark", m=77)))
        self.assert_refused(self.go(ticket(u="nobody_here", m=MB_NO)))
        self.assertFalse(User.objects.filter(username__iexact="nobody_here").exists())

    def test_inactive_staff_superuser_refused(self):
        for field in ("is_staff", "is_superuser"):
            setattr(self.user, field, True)
            self.user.save()
            self.assert_refused(self.go(ticket()))
            setattr(self.user, field, False)
            self.user.save()
        self.user.is_active = False
        self.user.save()
        self.assert_refused(self.go(ticket()))

    def test_locked_account_refused_like_id_login(self):
        rf = self.client.get("/start").wsgi_request
        for _ in range(LOGIN_FAILS_ID):
            _count_fail(rf, "OldKim")
        self.assert_refused(self.go(ticket()))

    def test_same_person_goes_straight_in(self):
        self.client.force_login(self.user)
        self.assert_in(self.go(ticket()))

    def test_other_person_is_asked_first_and_ticket_kept(self):
        other = User.objects.create(username="OtherLee", first_name="이다른")
        c = Client(enforce_csrf_checks=True)
        c.force_login(other)
        c.cookies[DEVICE_COOKIE] = DeviceToken.issue(other)
        t = ticket()
        r = self.go(t, c)
        self.assertContains(r, "지금 이다른 님으로 들어와 있어요.")
        self.assertContains(r, "김옛날 님으로 바꿀까요?")
        self.assertNotContains(r, "OldKim")  # 이름만
        self.assertEqual(c.session["_auth_user_id"], str(other.pk))  # 아직 안 바뀜
        self.assertContains(self.go(t, c), "바꿀까요?")  # 입장권을 아직 쓰지 않았다
        self.assertEqual(c.post("/auth/legacy", {"t": t}).status_code, 403)  # CSRF 없으면 거부
        self.assertEqual(c.session["_auth_user_id"], str(other.pk))
        token = c.cookies["csrftoken"].value
        r = c.post("/auth/legacy", {"t": t, "csrfmiddlewaretoken": token})
        self.assert_in(r, c)
        self.assertFalse(DeviceToken.objects.filter(user=other).exists())  # 이 PC의 앞사람 기억도 끊긴다
        c.force_login(other)
        token = c.cookies["csrftoken"].value  # 로그인이 CSRF 토큰을 바꾼다
        self.assertRedirects(c.post("/auth/legacy", {"t": t, "csrfmiddlewaretoken": token}), "/start",
                             fetch_redirect_response=False)  # 한 번 쓴 입장권
        self.assertEqual(c.session["_auth_user_id"], str(other.pk))

    def test_switch_post_after_expiry_goes_to_login(self):
        other = User.objects.create(username="OtherLee", first_name="이다른")
        self.client.force_login(other)
        t = ticket()
        self.assertContains(self.go(t), "바꿀까요?")
        with mock.patch("accounts.views.time.time", return_value=time.time() + 61):
            r = self.client.post("/auth/legacy", {"t": t})
        self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(other.pk))

    def test_failure_keeps_current_session(self):
        self.client.force_login(self.user)
        self.go(ticket(key="bad-key-0123456789abcdef0123456789ab"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_failure_page_does_not_say_why(self):
        r = self.go(ticket(u="nobody_here"), Client())
        self.assertNotIn(b"nobody_here", r.content)
        self.assertEqual(r.content, self.go(ticket(m=999), Client()).content)


class LegacySsoOffTests(TestCase):
    @REDIS_STANDIN
    @override_settings(LEGACY_SSO_KEY="")
    def test_empty_key_is_404(self):
        self.assertEqual(self.client.get("/auth/legacy", {"t": ticket(key="")}).status_code, 404)

    @REDIS_STANDIN
    @override_settings(LEGACY_SSO_KEY="x" * 31)
    def test_short_key_is_404(self):
        self.assertEqual(self.client.get("/auth/legacy", {"t": ticket(key="x" * 31)}).status_code, 404)

    @override_settings(LEGACY_SSO_KEY=KEY)
    def test_without_redis_is_404(self):
        """다시 쓰기 막기가 프로세스마다 따로 노는 메모리 캐시면 켜지 않는다(로컬·테스트 기본 설정)."""
        self.assertEqual(self.client.get("/auth/legacy", {"t": ticket()}).status_code, 404)

    @REDIS_STANDIN
    @override_settings(LEGACY_SSO_KEY=KEY)
    def test_put_not_allowed(self):
        self.assertEqual(self.client.put("/auth/legacy").status_code, 405)
