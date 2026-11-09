"""G3 2차: 로그인·초대 입장에서 '이 PC 기억하기(1년)' 선택(S2), 받는 사람 세션은 브라우저를 닫으면 끝(같은 PC 다음 사람).

불변식
- 시작 화면과 초대 입장 화면의 '이 PC 기억하기(1년)' 체크는 기본으로 꺼져 있다(사용자 결정 11/3, 공용 PC 보호).
  '이 PC에서 계속 쓰면 켜 두세요' 안내가 있다. 선택 값 없이 오면 서버도 끔으로 본다.
- 끄고 들어오면 기기 토큰이 없고(DB에도 없음) 세션은 브라우저를 닫으면 끝난다. 이 PC의 앞사람 기기 토큰도 지운다.
- 켜고 들어오면 지금처럼 1년 기기 토큰 + 기본 세션.
- 구글 콜백까지 선택이 state와 함께 세션으로 간다. 선택 없이(잇기 단추) 오면 이 PC의 지금 상태(기기 쿠키 있음/없음)를 따른다.
- 로그인 안 한 받는 사람(/c/…)의 세션은 브라우저를 닫으면 끝나고, 2시간이 지나면 미리 채움·이름을 잊는다.
  로그인한 회원이 /c/에서 내도 세션 설정은 그대로.
"""
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from collect.models import Collection, Field, Row
from spaces.models import Member, School, Space
from spaces.test_invite import InviteBase

from .models import DEVICE_COOKIE, DeviceToken, hash_token
from .tests import GOOGLE, INFO, TOKEN

User = get_user_model()


def close_browser(client):
    """브라우저를 닫으면 만료 없는 쿠키(세션 쿠키)가 사라진다."""
    for k in [k for k, m in client.cookies.items() if not m["max-age"] and not m["expires"]]:
        del client.cookies[k]


@override_settings(**GOOGLE)
class GoogleRememberTests(TestCase):
    def login(self, client=None, query="", info=INFO):
        client = client or self.client
        r = client.get("/auth/google/login" + query)
        state = parse_qs(urlparse(r["Location"]).query)["state"][0]
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, info]):
            return client.get("/auth/google/callback", {"state": state, "code": "c"})

    def test_start_has_remember_unchecked_and_hint(self):
        r = self.client.get("/start")
        self.assertContains(r, 'action="/auth/login"')  # P1: 아이디 칸과 구글 단추가 한 폼
        self.assertRegex(r.content.decode(), r'<input type="checkbox" name="remember" value="1">')
        self.assertNotRegex(r.content.decode(), r'name="remember" value="1" checked')
        self.assertContains(r, "이 PC 기억하기(1년)")
        self.assertContains(r, "이 PC에서 계속 쓰면 켜 두세요")

    def test_remember_off_no_device_token_browser_session(self):
        r = self.login(query="?remember=0")
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(DEVICE_COOKIE, r.cookies)
        self.assertFalse(DeviceToken.objects.exists())
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.get("/start").status_code, 302)  # 로그인은 됨
        close_browser(self.client)
        self.assertEqual(self.client.get("/start").status_code, 200)  # 닫으면 끝

    def test_remember_on_form_values_issue_device(self):
        r = self.login(query="?remember=0&remember=1")  # 숨은 0 + 체크 1
        self.assertIn(DEVICE_COOKIE, r.cookies)
        self.assertEqual(DeviceToken.objects.count(), 1)
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_remember_off_clears_previous_pc_token(self):
        self.login(query="?remember=1")
        old = self.client.cookies[DEVICE_COOKIE].value
        r = self.login(query="?remember=0", info={**INFO, "sub": "222", "name": "다른 사람"})
        self.assertFalse(DeviceToken.objects.filter(token_hash=hash_token(old)).exists())
        self.assertEqual(r.cookies[DEVICE_COOKIE].value, "")  # 지우는 쿠키
        close_browser(self.client)
        self.assertEqual(self.client.get("/start").status_code, 200)

    def test_choice_travels_in_session_not_callback_query(self):
        r = self.client.get("/auth/google/login?remember=0")
        state = parse_qs(urlparse(r["Location"]).query)["state"][0]
        self.assertNotIn("remember", r["Location"])
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, INFO]):
            r = self.client.get("/auth/google/callback", {"state": state, "code": "c", "remember": "1"})
        self.assertNotIn(DEVICE_COOKIE, r.cookies)

    def test_without_choice_defaults_off(self):
        r = self.login()  # 선택 값 없이 부르면 화면 기본값(끔)
        self.assertNotIn(DEVICE_COOKIE, r.cookies)
        self.assertFalse(DeviceToken.objects.exists())
        self.assertTrue(self.client.session.get_expire_at_browser_close())


@override_settings(**GOOGLE)
class JoinRememberTests(InviteBase):
    def go(self, client, data):
        client.get(f"/j/{self.space.invite_token}")
        return client.post("/join", data)

    def test_guest_link_follows_this_pc(self):
        """손님이 '구글 계정으로 이어 두기'(선택 없음)를 누르면 입장 때 고른 대로."""
        for remember, has in (("0", False), ("1", True)):
            c = Client()
            self.go(c, {"cls": "4-2" if has else "4-3", "name": f"손님{remember}", "remember": remember})
            r = c.get("/auth/google/login?next=/")
            state = parse_qs(urlparse(r["Location"]).query)["state"][0]
            with mock.patch("accounts.views._http_json", side_effect=[TOKEN, {**INFO, "sub": f"g{remember}"}]):
                r = c.get("/auth/google/callback", {"state": state, "code": "c"})
            self.assertEqual(bool(r.cookies.get(DEVICE_COOKIE) and r.cookies[DEVICE_COOKIE].value), has, remember)
            self.assertEqual(c.session.get_expire_at_browser_close(), not has)

    def test_join_shows_unchecked_box_and_hint(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        r = c.get("/join")
        self.assertRegex(r.content.decode(), r'<input type="checkbox" name="remember" value="1">')
        self.assertNotRegex(r.content.decode(), r'name="remember" value="1" checked')
        self.assertContains(r, "이 PC 기억하기(1년)")
        self.assertContains(r, "이 PC에서 계속 쓰면 켜 두세요")

    def test_join_without_choice_is_off(self):
        c = Client()
        r = self.go(c, {"cls": "4-2", "name": "정하은"})
        self.assertNotIn(DEVICE_COOKIE, r.cookies)
        self.assertTrue(c.session.get_expire_at_browser_close())

    def test_join_off_no_device_browser_session(self):
        c = Client()
        r = self.go(c, {"cls": "4-2", "name": "정하은", "remember": "0"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(DEVICE_COOKIE, r.cookies)
        self.assertFalse(DeviceToken.objects.exists())
        self.assertEqual(c.get(f"/s/{self.space.token}/").status_code, 200)
        close_browser(c)
        self.assertEqual(c.get(f"/s/{self.space.token}/").status_code, 302)

    def test_join_on(self):
        c = Client()
        r = self.go(c, {"cls": "4-2", "name": "정하은", "remember": ["0", "1"]})
        self.assertIn(DEVICE_COOKIE, r.cookies)
        self.assertFalse(c.session.get_expire_at_browser_close())

    def test_error_keeps_choice(self):
        c = Client()
        r = self.go(c, {"cls": "", "name": "정하은", "remember": ["0", "1"]})
        self.assertEqual(r.status_code, 400)
        self.assertRegex(r.content.decode(), r'name="remember" value="1" checked')


class RecipientSessionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999998", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.u = User.objects.create(username="google:m")
        cls.m = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.u)
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.m, title="참가", source="sheet",
                                          unit_head=["학년반", "담임"])
        Field.objects.create(collection=cls.c, pos=0, name="메모")
        cls.r = Row.objects.create(collection=cls.c, pos=0, unit="4-2", who="박서준")

    def url(self):
        return f"/c/{self.c.link_token}/u/{self.r.key}"

    def test_anonymous_session_ends_with_browser(self):
        r = self.client.post(self.url(), {"f0": "비밀메모", "by": "박서준"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertContains(self.client.get(self.url()), "비밀메모")  # 같은 사람 바로 고치기는 그대로
        close_browser(self.client)  # 같은 PC 다음 사람
        r = self.client.get(self.url())
        self.assertNotContains(r, "비밀메모")
        self.assertNotContains(r, 'value="박서준"')

    def test_anonymous_forgets_after_two_hours(self):
        self.client.post(self.url(), {"f0": "비밀메모", "by": "박서준"})
        with mock.patch("collect.submit.time.time", return_value=__import__("time").time() + 2 * 3600 + 5):
            r = self.client.get(self.url())
        self.assertNotContains(r, "비밀메모")

    def test_member_session_untouched(self):
        self.client.force_login(self.u)
        self.client.post(self.url(), {"f0": "회원메모", "by": "김가람"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        with mock.patch("collect.submit.time.time", return_value=__import__("time").time() + 3 * 3600):
            self.assertContains(self.client.get(self.url()), "회원메모")
