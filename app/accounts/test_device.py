"""G3 S2 기기 토큰 되돌리기: 설정 화면 '이 PC 1년 기억' 스위치 + '모든 기기에서 로그아웃'."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from collect.models import PushSubscription
from spaces.models import Member, School, Space

from .models import DEVICE_COOKIE, DeviceToken

User = get_user_model()
SITE = "https://t.goteacher.xyz"


@override_settings(SITE_URL=SITE)
class DeviceSettingsTests(TestCase):
    def setUp(self):
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2", "3"])
        p.start()
        self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.space = Space.objects.create(school=school, grade=4, name="새솔초 4학년")
        self.other = Space.objects.create(school=school, grade=5, name="새솔초 5학년")
        self.user = User.objects.create(username="google:1", first_name="송민재")
        self.user.set_unusable_password()
        self.user.save()
        self.me = Member.objects.create(space=self.space, user=self.user, name="송민재", cls="4-1")
        self.url = f"/s/{self.space.token}/settings"

    def pc(self):
        """기기 토큰으로 기억된 PC 하나."""
        c = Client()
        c.cookies[DEVICE_COOKIE] = DeviceToken.issue(self.user)
        return c

    def test_settings_page_shows_switch_on_with_until_date_and_menu(self):
        c = self.pc()
        r = c.get(self.url)
        self.assertContains(r, "이 PC 1년 기억")
        self.assertContains(r, 'name="remember" value="1" checked')
        self.assertContains(r, "까지")
        self.assertContains(r, "모든 기기에서 로그아웃")
        self.assertContains(r, f'href="/s/{self.space.token}/settings" aria-current="page"')
        self.assertContains(self.pc().get(f"/s/{self.space.token}/"), f'href="/s/{self.space.token}/settings"')

    def test_other_space_settings_is_404(self):
        self.assertEqual(self.pc().get(f"/s/{self.other.token}/settings").status_code, 404)
        self.assertEqual(self.pc().post(f"/s/{self.other.token}/settings/remember").status_code, 404)
        self.assertEqual(self.pc().post(f"/s/{self.other.token}/settings/logout-all").status_code, 404)

    def test_switch_off_drops_this_pc_token_and_session_ends_at_browser_close(self):
        c = self.pc()
        other_pc = self.pc()
        r = c.post(f"{self.url}/remember", {}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.cookies[DEVICE_COOKIE].value, "")  # 쿠키 지움
        self.assertEqual(DeviceToken.objects.filter(user=self.user).count(), 1)  # 다른 PC 것은 그대로
        self.assertNotContains(r, "checked")
        self.assertTrue(c.session.get_expire_at_browser_close())
        # 세션은 살아 있어 계속 쓸 수 있다
        self.assertEqual(c.get(self.url).status_code, 200)
        self.assertEqual(other_pc.get(self.url).status_code, 200)

    def test_switch_on_issues_token_and_persistent_session(self):
        c = self.pc()
        c.post(f"{self.url}/remember", {})
        r = c.post(f"{self.url}/remember", {"remember": "1"}, headers={"HX-Request": "true"})
        self.assertTrue(r.cookies[DEVICE_COOKIE].value)
        self.assertTrue(r.cookies[DEVICE_COOKIE]["httponly"])
        self.assertContains(r, 'value="1" checked')
        self.assertFalse(c.session.get_expire_at_browser_close())
        self.assertEqual(DeviceToken.objects.filter(user=self.user).count(), 1)

    def test_logout_all_drops_every_token_session_and_push(self):
        a, b = self.pc(), self.pc()
        a.get(self.url), b.get(self.url)  # 둘 다 세션이 생김
        PushSubscription.objects.create(space=self.space, member=self.me, endpoint="https://fcm.googleapis.com/x",
                                        p256dh="k", auth="a")
        stranger = User.objects.create(username="google:2")
        DeviceToken.issue(stranger)
        r = a.post(f"{self.url}/logout-all")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.cookies[DEVICE_COOKIE].value, "")
        self.assertFalse(DeviceToken.objects.filter(user=self.user).exists())
        self.assertTrue(DeviceToken.objects.filter(user=stranger).exists())
        self.assertFalse(PushSubscription.objects.filter(member=self.me).exists())
        # 다른 PC의 세션도 끊긴다(쿠키 토큰도 없음 → 로그인 화면으로)
        r = b.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/start"))
        self.assertTrue(a.get(self.url)["Location"].startswith("/start"))

    def test_logout_all_needs_post(self):
        self.assertEqual(self.pc().get(f"{self.url}/logout-all").status_code, 405)
