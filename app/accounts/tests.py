"""B4 구글 로그인 + 기기 토큰. 구글 응답은 accounts.views._http_json을 목으로 바꾼다."""
import base64
import hashlib
from datetime import timedelta
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import DEVICE_COOKIE, DeviceToken, hash_token

User = get_user_model()

GOOGLE = dict(
    GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
    GOOGLE_CLIENT_SECRET="test-secret",
    SITE_URL="http://localhost:8000",
)
TOKEN = {"access_token": "at-123", "id_token": "x.y.z", "token_type": "Bearer"}
INFO = {"sub": "1098765", "email": "kim@school.kr", "email_verified": True, "name": "김가람", "picture": "https://x/p.png"}


@override_settings(**GOOGLE)
class GoogleLoginTests(TestCase):
    def start(self, next_url=None):
        url = "/auth/google/login" + (f"?next={next_url}" if next_url else "")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        q = parse_qs(urlparse(r["Location"]).query)
        return r, q["state"][0]

    def callback(self, state, code="code-abc", token=TOKEN, info=INFO):
        with mock.patch("accounts.views._http_json", side_effect=[token, info]) as m:
            r = self.client.get("/auth/google/callback", {"state": state, "code": code})
        return r, m

    def test_login_redirects_to_google_with_site_url_callback(self):
        r, state = self.start()
        loc = urlparse(r["Location"])
        q = parse_qs(loc.query)
        self.assertEqual(loc.netloc, "accounts.google.com")
        self.assertEqual(q["redirect_uri"], ["http://localhost:8000/auth/google/callback"])
        self.assertEqual(q["client_id"], [GOOGLE["GOOGLE_CLIENT_ID"]])
        self.assertEqual(q["scope"], ["openid email profile"])
        self.assertGreaterEqual(len(state), 32)

    def test_state_mismatch_rejected(self):
        self.start()
        r, m = self.callback("forged-state")
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn(DEVICE_COOKIE, r.cookies)

    def test_callback_without_started_login_rejected(self):
        r, m = self.callback("anything")
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_state_is_single_use(self):
        _, state = self.start()
        self.callback(state)
        self.client.logout()
        r, m = self.callback(state)
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_success_logs_in_and_keeps_only_sub_email_name(self):
        _, state = self.start()
        r, m = self.callback(state)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/")
        user = User.objects.get()
        self.assertEqual(user.username, "google:1098765")
        self.assertEqual((user.email, user.first_name), ("kim@school.kr", "김가람"))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # 토큰 교환 요청에 code와 SITE_URL 기반 redirect_uri가 실린다
        body = parse_qs(m.call_args_list[0].args[0].data.decode())
        self.assertEqual(body["code"], ["code-abc"])
        self.assertEqual(body["redirect_uri"], ["http://localhost:8000/auth/google/callback"])
        self.assertEqual(m.call_args_list[1].args[0].get_header("Authorization"), "Bearer at-123")
        self.assertContains(self.client.get("/"), "김가람")

    def test_device_cookie_is_long_httponly_and_hashed_in_db(self):
        _, state = self.start()
        r, _ = self.callback(state)
        c = r.cookies[DEVICE_COOKIE]
        raw = c.value
        self.assertGreaterEqual(len(raw), 32)
        self.assertTrue(c["httponly"])
        self.assertEqual(c["samesite"], "Lax")
        self.assertEqual(int(c["max-age"]), 365 * 24 * 3600)
        t = DeviceToken.objects.get()
        self.assertEqual(t.token_hash, hash_token(raw))
        self.assertFalse(DeviceToken.objects.filter(token_hash=raw).exists())

    def test_same_google_account_reuses_user(self):
        _, state = self.start()
        self.callback(state)
        _, state = self.start()
        self.callback(state, info={**INFO, "name": "김가람2"})
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().first_name, "김가람2")

    def test_next_kept_but_external_next_dropped(self):
        _, state = self.start("/s/abc/")
        r, _ = self.callback(state)
        self.assertEqual(r["Location"], "/s/abc/")
        self.client.logout()
        _, state = self.start("https://evil.example/")
        r, _ = self.callback(state)
        self.assertEqual(r["Location"], "/")

    def test_google_failure_is_502_and_not_logged_in(self):
        _, state = self.start()
        with mock.patch("accounts.views._http_json", side_effect=URLError("down")):
            r = self.client.get("/auth/google/callback", {"state": state, "code": "secret-code"})
        self.assertEqual(r.status_code, 502)
        self.assertNotContains(r, "secret-code", status_code=502)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_google_error_param_rejected(self):
        _, state = self.start()
        with mock.patch("accounts.views._http_json") as m:
            r = self.client.get("/auth/google/callback", {"state": state, "error": "access_denied"})
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_missing_client_id_is_503(self):
        self.assertEqual(self.client.get("/auth/google/login").status_code, 503)


@override_settings(**GOOGLE)
class DeviceTokenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="google:1", first_name="이나래", email="lee@school.kr")

    def test_device_cookie_alone_reidentifies(self):
        raw = DeviceToken.issue(self.user)
        self.client.cookies[DEVICE_COOKIE] = raw  # 세션 쿠키 없음(브라우저를 닫았다 연 상태)
        r = self.client.get("/")
        self.assertContains(r, "이나래")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_forged_device_cookie_rejected_and_cleared(self):
        DeviceToken.issue(self.user)
        self.client.cookies[DEVICE_COOKIE] = "x" * 43
        r = self.client.get("/")
        self.assertNotContains(r, "이나래")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(r.cookies[DEVICE_COOKIE].value, "")

    def test_hash_in_cookie_is_not_accepted(self):
        raw = DeviceToken.issue(self.user)
        self.client.cookies[DEVICE_COOKIE] = hash_token(raw)  # DB가 새도 해시로는 못 들어온다
        self.assertNotContains(self.client.get("/"), "이나래")

    def test_expired_device_token_rejected(self):
        raw = DeviceToken.issue(self.user)
        DeviceToken.objects.update(created_at=timezone.now() - timedelta(days=366))
        self.client.cookies[DEVICE_COOKIE] = raw
        self.assertNotContains(self.client.get("/"), "이나래")

    def test_logout_revokes_this_device(self):
        raw = DeviceToken.issue(self.user)
        other = DeviceToken.issue(self.user)
        self.client.cookies[DEVICE_COOKIE] = raw
        self.client.get("/")
        r = self.client.post("/auth/logout")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.cookies[DEVICE_COOKIE].value, "")
        self.assertFalse(DeviceToken.objects.filter(token_hash=hash_token(raw)).exists())
        self.assertTrue(DeviceToken.objects.filter(token_hash=hash_token(other)).exists())
        self.client.cookies[DEVICE_COOKIE] = raw  # 지운 토큰을 다시 넣어도 안 된다
        self.assertNotContains(self.client.get("/"), "이나래")

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/auth/logout").status_code, 405)

    def test_inactive_user_not_reidentified(self):
        raw = DeviceToken.issue(self.user)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.client.cookies[DEVICE_COOKIE] = raw
        self.assertNotContains(self.client.get("/"), "이나래")


@override_settings(**GOOGLE)
class QAEdgeTests(TestCase):
    """QA 추가(B4): 기존 테스트에 없던 경계 사례."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="google:9", first_name="박다솜", email="park@school.kr")

    def login_flow(self, next_url=None):
        url = "/auth/google/login" + (f"?next={next_url}" if next_url else "")
        state = parse_qs(urlparse(self.client.get(url)["Location"]).query)["state"][0]
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, INFO]):
            return self.client.get("/auth/google/callback", {"state": state, "code": "c"})

    @override_settings(SITE_URL="https://t.goteacher.xyz")
    def test_device_cookie_secure_on_https(self):
        r = self.login_flow()
        self.assertTrue(r.cookies[DEVICE_COOKIE]["secure"])

    def test_state_from_other_browser_rejected(self):
        # 공격자가 자기 브라우저에서 받은 state를 피해자 브라우저 콜백에 끼워 넣는다
        state = parse_qs(urlparse(self.client.get("/auth/google/login")["Location"]).query)["state"][0]
        victim = self.client_class()
        victim.get("/auth/google/login")
        with mock.patch("accounts.views._http_json") as m:
            r = victim.get("/auth/google/callback", {"state": state, "code": "c"})
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_session_key_rotates_on_login(self):
        self.client.get("/auth/google/login")
        before = self.client.session.session_key
        self.login_flow()
        self.assertNotEqual(self.client.session.session_key, before)

    def test_open_redirect_variants_dropped(self):
        for bad in ("//evil.example/", "/\\evil.example/", "https:evil.example", "javascript:alert(1)"):
            self.client.logout()
            r = self.login_flow(next_url=bad.replace("\\", "%5C"))
            self.assertEqual(r["Location"], "/", bad)

    def test_userinfo_without_sub_is_502(self):
        state = parse_qs(urlparse(self.client.get("/auth/google/login")["Location"]).query)["state"][0]
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, {"email": "x@y"}]):
            r = self.client.get("/auth/google/callback", {"state": state, "code": "c"})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(User.objects.filter(username__startswith="google:1098765").count(), 0)

    def test_overlong_device_cookie_ignored(self):
        self.client.cookies[DEVICE_COOKIE] = "a" * 500
        self.assertNotContains(self.client.get("/"), "박다솜")

    def test_device_token_is_bound_to_its_user(self):
        other = User.objects.create(username="google:10", first_name="최하늘")
        self.client.cookies[DEVICE_COOKIE] = DeviceToken.issue(other)
        r = self.client.get("/")
        self.assertContains(r, "최하늘")
        self.assertNotContains(r, "박다솜")

    def test_reidentified_on_each_browser_restart(self):
        raw = DeviceToken.issue(self.user)
        for _ in range(2):
            c = self.client_class()
            c.cookies[DEVICE_COOKIE] = raw  # 세션 쿠키 없이 매번 새 브라우저
            self.assertContains(c.get("/"), "박다솜")
        self.assertEqual(DeviceToken.objects.count(), 1)  # 재식별이 토큰을 늘리지 않는다

    def test_logout_needs_csrf(self):
        c = self.client_class(enforce_csrf_checks=True)
        c.force_login(self.user)
        self.assertEqual(c.post("/auth/logout").status_code, 403)

    def test_logout_after_restart_with_csrf_works(self):
        # 브라우저 재시작 뒤(세션 없음, 기기 쿠키+CSRF 쿠키만) 로그아웃 버튼이 동작해야 한다
        raw = DeviceToken.issue(self.user)
        c = self.client_class(enforce_csrf_checks=True)
        c.cookies[DEVICE_COOKIE] = raw
        page = c.get("/")
        token = page.context["csrf_token"]
        c.cookies.pop("sessionid", None)  # 세션도 날아갔다고 가정
        r = c.post("/auth/logout", {"csrfmiddlewaretoken": str(token)})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(DeviceToken.objects.exists())

    def test_home_next_sanitized(self):
        r = self.client.get("/", {"next": "https://evil.example/"})
        self.assertNotContains(r, "evil.example")

    def test_no_old_name_on_home(self):
        self.assertNotContains(self.client.get("/"), "티로드")
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get("/"), "티로드")


@override_settings(**GOOGLE)
class SecurityFollowupTests(TestCase):
    """B4 보안 검토 후속: 재로그인 토큰 교체, CSRF 회전, PKCE, email_verified."""

    def start(self):
        r = self.client.get("/auth/google/login")
        return parse_qs(urlparse(r["Location"]).query)

    def callback(self, state, info=INFO):
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, info]) as m:
            r = self.client.get("/auth/google/callback", {"state": state, "code": "c"})
        return r, m

    def test_relogin_replaces_old_device_token(self):
        r, _ = self.callback(self.start()["state"][0])
        old = r.cookies[DEVICE_COOKIE].value
        self.client.logout()  # 세션만 끊고 기기 쿠키는 남은 채 다시 로그인
        self.client.cookies[DEVICE_COOKIE] = old
        r, _ = self.callback(self.start()["state"][0])
        new = r.cookies[DEVICE_COOKIE].value
        self.assertNotEqual(old, new)
        self.assertEqual(list(DeviceToken.objects.values_list("token_hash", flat=True)), [hash_token(new)])

    def test_relogin_keeps_other_pcs_tokens(self):
        user = User.objects.create(username="google:1098765")
        other_pc = DeviceToken.issue(user)
        self.callback(self.start()["state"][0])
        self.assertTrue(DeviceToken.objects.filter(token_hash=hash_token(other_pc)).exists())
        self.assertEqual(DeviceToken.objects.count(), 2)

    def test_form_opened_before_session_loss_still_posts(self):
        # 로그인 상태에서 폼을 열어 두었는데 세션이 끊김 → 기기 쿠키 재식별(login()이 CSRF 회전) 후에도 그 폼 POST가 통과
        user = User.objects.create(username="google:2", first_name="정한결")
        raw = DeviceToken.issue(user)
        c = self.client_class(enforce_csrf_checks=True)
        c.force_login(user)
        c.cookies[DEVICE_COOKIE] = raw
        token = str(c.get("/").context["csrf_token"])
        c.cookies.pop("sessionid")
        r = c.post("/auth/logout", {"csrfmiddlewaretoken": token})
        self.assertEqual(r.status_code, 302)
        self.assertIn("csrftoken", r.cookies)  # 회전은 그대로 일어난다(새 토큰 발급)

    def test_pkce_s256_challenge_and_verifier_used_once(self):
        q = self.start()
        self.assertEqual(q["code_challenge_method"], ["S256"])
        verifier = self.client.session["google_verifier"]
        self.assertGreaterEqual(len(verifier), 43)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], [expected])
        r, m = self.callback(q["state"][0])
        body = parse_qs(m.call_args_list[0].args[0].data.decode())
        self.assertEqual(body["code_verifier"], [verifier])
        self.assertNotIn("google_verifier", self.client.session)

    def test_callback_without_verifier_rejected(self):
        state = self.start()["state"][0]
        s = self.client.session
        del s["google_verifier"]
        s.save()
        r, m = self.callback(state)
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_unverified_email_not_stored(self):
        for flag in (False, None):
            info = {k: v for k, v in INFO.items() if k != "email_verified"}
            if flag is not None:
                info["email_verified"] = flag
            self.client.logout()
            self.callback(self.start()["state"][0], info=info)
            self.assertEqual(User.objects.get().email, "", flag)
