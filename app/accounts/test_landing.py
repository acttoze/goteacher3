"""G5 소개 페이지: 로그인 전 / = design/landing.html, 시작 화면은 /start."""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from spaces.models import Member, School, Space

User = get_user_model()


def _space():
    school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
    return Space.objects.create(school=school, grade=4, name="4학년")


class LandingTests(TestCase):
    def test_anonymous_root_is_landing(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "landing.html")
        for text in ("한글 양식", "교직원 정보만", "오라클 클라우드", "국내", "동학년판 무료", "12월 14일(월)"):
            self.assertContains(r, text)

    def test_start_buttons_go_to_start(self):
        html = self.client.get("/").content.decode()
        self.assertGreaterEqual(html.count('href="/start"'), 3)  # 위 줄·첫 화면·마무리·바닥

    def test_start_page_is_google_start(self):
        r = self.client.get("/start")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "구글 계정으로 시작하기")
        self.assertContains(r, 'href="/auth/google/login"')

    def test_start_keeps_safe_next(self):
        r = self.client.get("/start", {"next": "/s/abc/"})
        self.assertContains(r, "/auth/google/login?next=/s/abc/")

    def test_legal_links_and_contact(self):
        html = self.client.get("/").content.decode()
        self.assertIn('href="/privacy"', html)
        self.assertIn('href="/terms"', html)
        self.assertIn('href="mailto:admin@goteacher.xyz"', html)
        self.assertNotIn("hello@", html)
        self.assertNotIn("티로드", html)
        for url in ("/privacy", "/terms"):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_no_empty_or_missing_links(self):
        html = self.client.get("/").content.decode()
        hrefs = re.findall(r'href="([^"]*)"', html)
        self.assertTrue(hrefs)
        for h in hrefs:
            self.assertTrue(h.strip() and h != "#", h)
            self.assertNotRegex(h, r"\.html$")  # design의 today.html·forms.html·start.html이 남지 않게
        for anchor in {h[1:] for h in hrefs if h.startswith("#")}:
            self.assertIn(f'id="{anchor}"', html)
        self.assertNotIn("<form", html)  # 메일 수집 폼 없음(저장 기능이 없다)

    def test_logged_in_root_unchanged(self):
        u = User.objects.create(username="google:9", first_name="송민재")
        self.client.force_login(u)
        r = self.client.get("/")
        self.assertTemplateUsed(r, "spaces/start_school.html")  # 동학년 없으면 학교 찾기
        space = _space()
        Member.objects.create(space=space, user=u, name="송민재", cls="4-1")
        self.assertEqual(self.client.get("/")["Location"], f"/s/{space.token}/today")  # G1: 첫 화면은 오늘

    def test_login_required_goes_to_start(self):
        space = _space()
        r = self.client.get(f"/s/{space.token}/")
        self.assertEqual(r["Location"], f"/start?next=/s/{space.token}/")


class LandingQAEdgeTests(TestCase):
    """QA 추가(G5): 링크를 실제로 따라가는 흐름과 경계 사례."""

    def _follow_to_google(self, start_location):
        """/start?next=... 화면의 구글 버튼 href를 그대로 따라가 google_login까지 간다."""
        html = self.client.get(start_location).content.decode()
        href = re.search(r'class="gbtn" href="([^"]+)"', html).group(1).replace("&amp;", "&")
        return self.client.get(href)

    def test_full_flow_returns_to_original_next_with_query(self):
        from unittest import mock
        from urllib.parse import parse_qs, urlparse

        from django.test import override_settings

        from accounts.tests import GOOGLE, INFO, TOKEN

        space = _space()
        target = f"/s/{space.token}/members?x=1&y=2"
        r = self.client.get(target)  # 로그인 필요한 화면 → /start?next=
        self.assertTrue(r["Location"].startswith("/start?next="))
        with override_settings(**GOOGLE):
            r = self._follow_to_google(r["Location"])
            self.assertEqual(urlparse(r["Location"]).netloc, "accounts.google.com")
            state = parse_qs(urlparse(r["Location"]).query)["state"][0]
            with mock.patch("accounts.views._http_json", side_effect=[TOKEN, INFO]):
                r = self.client.get("/auth/google/callback", {"state": state, "code": "c"})
        self.assertEqual(r["Location"], target)  # 쿼리 문자열까지 원래 자리로

    def test_start_drops_unsafe_next_variants(self):
        for bad in ("//evil.example/", "/\\evil.example/", "https:evil.example", "javascript:alert(1)",
                    "http://evil.example/s/x/"):
            html = self.client.get("/start", {"next": bad}).content.decode()
            self.assertIn('href="/auth/google/login"', html, bad)  # next 없이
            self.assertNotIn("evil", html, bad)
            self.assertNotIn("javascript:", html, bad)

    def test_guest_root_is_start_not_landing(self):
        g = User.objects.create(username="guest:abc", first_name="손님")
        self.client.force_login(g)
        r = self.client.get("/")
        self.assertTemplateNotUsed(r, "landing.html")
        self.assertContains(r, "구글 계정으로 시작하기")

    def test_logout_lands_on_landing(self):
        u = User.objects.create(username="google:7", first_name="정하은")
        self.client.force_login(u)
        r = self.client.post("/auth/logout")
        self.assertEqual(r["Location"], "/")
        self.assertTemplateUsed(self.client.get("/"), "landing.html")

    def test_landing_only_get(self):
        self.assertEqual(self.client.post("/").status_code, 405)
        self.assertEqual(self.client.post("/start").status_code, 405)

    def test_landing_static_assets_resolve(self):
        html = self.client.get("/").content.decode()
        self.assertIn("css/landing.css", html)
        self.assertIn("js/landing.js", html)
        self.assertNotIn("design/", html)
        self.assertNotIn("../prototype", html)


class StartRedirectTests(TestCase):
    """구글로 로그인한 사람이 /start를 열면 가려던 곳으로, 손님은 구글 잇기 화면 그대로."""

    def test_google_user_redirected(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create(username="google:s1"))
        r = self.client.get("/start?next=/s/abc/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/s/abc/"))

    def test_guest_sees_start(self):
        from accounts.models import new_guest
        self.client.force_login(new_guest("손님"))
        self.assertEqual(self.client.get("/start").status_code, 200)
