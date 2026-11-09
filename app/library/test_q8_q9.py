"""Q8 교내/교외 메뉴·안내 띠, Q9 robots·공개 화면·양식 추천."""
from unittest import mock
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import new_guest
from config.test_ratelimit import RL_CACHES
from partners.models import Partner
from spaces.models import Member, School, Space

from .models import FormDoc, FormLike


def _doc(src, title):
    return FormDoc.objects.create(src=src, slug=f"양식-{src}", category="학교", title=title, desc=f"{title} 설명", file=f"forms/{src}.hwpx", filename=f"{src}.hwpx", size=2048)


def _user(sub):
    return get_user_model().objects.create(username=f"google:{sub}", email=f"{sub}@example.com", first_name=sub)


class MenuTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="가람초등학교")
        self.grade = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.user = _user("g")
        Member.objects.create(space=self.grade, name="김동학", cls="4-1", user=self.user)
        self.client.force_login(self.user)

    def test_grade_frame_has_groups_no_it(self):
        r = self.client.get(f"/s/{self.grade.token}/today")
        html = r.content.decode()
        self.assertIn('<p class="group">교내</p>', html)
        self.assertIn('<p class="group">교외</p>', html)
        self.assertLess(html.index('<p class="group">교내</p>'), html.index(f'href="/s/{self.grade.token}/c/"'))
        self.assertLess(html.index('<p class="group">교외</p>'), html.index('href="/forms"'))
        self.assertIn('href="/community"', html)
        self.assertIn('href="/partners"', html)
        self.assertNotIn("/it\"", html)
        self.assertLess(html.index(f'href="/s/{self.grade.token}/c/"'), html.index('<p class="group">교외</p>'))

    def test_anonymous_frame_has_out_nav_and_login(self):
        c = self.client_class()
        for path, on in (("/forms", "/forms"), ("/partners", "/partners")):
            r = c.get(path)
            self.assertContains(r, 'class="out-nav"')
            self.assertContains(r, f'href="{on}" aria-current="page"')
            self.assertContains(r, 'href="/community"')
            self.assertContains(r, 'href="/start"')
            self.assertNotContains(r, "정보수리방")
            self.assertNotContains(r, 'class="side"')

    def test_other_bare_screens_have_no_out_nav(self):
        self.assertNotContains(self.client_class().get("/start"), "out-nav")

    def test_band_on_outside_screens(self):
        c = self.client_class()
        for path in ("/forms", "/partners"):
            self.assertContains(c.get(path), "로그인 없이 누구나 봐요")
        self.assertNotContains(self.client.get(f"/s/{self.grade.token}/today"), "학교 밖 공간")


class RobotsTests(TestCase):
    @override_settings(SITE_URL="https://t.goteacher.xyz")
    def test_robots(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/plain"))
        text = r.content.decode()
        for path in ("/s/", "/c/", "/v/", "/tv/", "/m/", "/n/", "/j/", "/community"):
            self.assertIn(f"Disallow: {path}\n", text)
        self.assertIn("Allow: /\n", text)
        self.assertIn("Sitemap: https://t.goteacher.xyz/sitemap.xml", text)
        self.assertNotIn("Disallow: /forms", text)
        self.assertNotIn("Disallow: /partners", text)
        self.assertEqual(self.client.post("/robots.txt").status_code, 405)


class PublicScreenTests(TestCase):
    def setUp(self):
        self.doc = _doc("f1", "출장 신청서")
        self.partner = Partner.objects.create(name="가나인쇄", cat=Partner._meta.get_field("cat").choices[0][0] if Partner._meta.get_field("cat").choices else "인쇄", area="전국", desc="인쇄 업체 소개", is_public=True)
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="비밀초등학교")
        self.space = Space.objects.create(school=school, grade=4, name="비밀 4학년")

    @override_settings(SITE_URL="https://t.goteacher.xyz")
    def test_anonymous_200_with_meta_and_no_space_leak(self):
        pages = [("/forms", "/forms"), (f"/forms/{self.doc.slug}", f"/forms/{quote(self.doc.slug)}"),
                 ("/partners", "/partners"), (f"/partners/{self.partner.slug}", f"/partners/{quote(self.partner.slug)}")]
        for path, canonical in pages:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertContains(r, '<meta name="description"')
            self.assertContains(r, f'<link rel="canonical" href="https://t.goteacher.xyz{canonical}">')
            self.assertContains(r, "<h1")
            self.assertContains(r, "· 고티처</title>")
            html = r.content.decode()
            for word in ("비밀초등학교", self.space.token, "/s/"):
                self.assertNotIn(word, html, path)

    def test_detail_h1_is_the_form(self):
        self.assertContains(self.client.get(f"/forms/{self.doc.slug}"), "<h1>출장 신청서</h1>", html=True)
        self.assertContains(self.client.get("/forms"), "<h1>양식 자료실</h1>", html=True)

    def test_meta_kept_in_shell(self):
        u = _user("m")
        Member.objects.create(space=self.space, name="김", cls="4-1", user=u)
        self.client.force_login(u)
        for path in ("/forms", f"/forms/{self.doc.slug}", "/partners", f"/partners/{self.partner.slug}"):
            r = self.client.get(path)
            self.assertContains(r, 'class="side"')
            self.assertContains(r, '<meta name="description"')
            self.assertContains(r, 'rel="canonical"')

    def test_sitemap_has_forms_and_partners(self):
        r = self.client.get("/sitemap.xml")
        self.assertContains(r, quote(self.doc.slug))
        self.assertContains(r, quote(self.partner.slug))

    def test_partner_guides_nonmembers(self):
        guide = "추천은 고티처 회원만 할 수 있어요"
        for path in ("/partners", f"/partners/{self.partner.slug}"):
            self.assertContains(self.client.get(path), guide)
        self.assertContains(self.client.get(f"/partners/{self.partner.slug}"), 'href="/start?next=%2Fpartners%2F')
        u = _user("m")
        self.client.force_login(u)
        for path in ("/partners", f"/partners/{self.partner.slug}"):
            self.assertNotContains(self.client.get(path), guide)


class LikeTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.a = _doc("f1", "가 양식")
        self.b = _doc("f2", "나 양식")
        self.user = _user("m")

    def url(self, doc=None):
        return f"/forms/{(doc or self.a).slug}/like"

    def test_anonymous_refused_and_guided(self):
        self.assertEqual(self.client.post(self.url()).status_code, 403)
        self.assertFalse(FormLike.objects.exists())
        r = self.client.get(f"/forms/{self.a.slug}")
        self.assertContains(r, "추천은 고티처 회원만 할 수 있어요")
        self.assertContains(r, "/start?next=%2Fforms%2F")
        self.assertNotContains(r, 'action="/forms/')

    def test_guest_refused(self):
        self.client.force_login(new_guest("손님"))
        self.assertEqual(self.client.post(self.url()).status_code, 403)
        self.assertFalse(FormLike.objects.exists())
        self.assertContains(self.client.get(f"/forms/{self.a.slug}"), "/start?next=")

    def test_toggle_one_vote(self):
        self.client.force_login(self.user)
        r = self.client.post(self.url())
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.a.likes.count(), 1)
        r = self.client.post(self.url(), HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.a.likes.count(), 0)
        r = self.client.post(self.url(), HTTP_HX_REQUEST="true")
        self.assertEqual(self.a.likes.count(), 1)
        self.assertContains(r, 'id="likeBox"')
        self.assertContains(r, 'aria-pressed="true"')
        self.assertContains(r, 'hx-swap-oob="true"')
        self.assertNotContains(r, "<html")

    def test_csrf_required(self):
        c = self.client_class(enforce_csrf_checks=True)
        c.force_login(self.user)
        self.assertEqual(c.post(self.url()).status_code, 403)
        self.assertFalse(FormLike.objects.exists())

    def test_get_not_allowed_and_unknown_404(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertEqual(self.client.post("/forms/없는-양식/like").status_code, 404)

    def test_count_shown_and_sort(self):
        for i in range(2):
            FormLike.objects.create(form=self.a, user=_user(f"u{i}"))
        FormLike.objects.create(form=self.b, user=self.user)
        html = self.client.get("/forms", {"sort": "likes"}).content.decode()
        self.assertLess(html.index("가 양식"), html.index("나 양식"))
        self.assertIn("추천 2", html)
        self.assertIn("추천 1", html)
        self.assertContains(self.client.get("/forms", {"sort": "likes"}), '<option value="likes" selected>')
        # 분류 탭·줄 링크가 정렬을 잇는다
        self.assertContains(self.client.get("/forms", {"sort": "likes"}), f'/forms/{self.a.slug}?sort=likes')
        self.assertEqual(self.client.get("/forms", {"sort": "엉뚱"}).status_code, 200)
        self.client.force_login(self.user)
        r = self.client.get(f"/forms/{self.b.slug}")
        self.assertContains(r, 'aria-pressed="true"')
        self.assertIn("no-store", r["Cache-Control"])

    def test_default_order_stays_newest_first(self):
        html = self.client.get("/forms").content.decode()  # Count를 붙여도 Meta.ordering이 빠지지 않게
        self.assertLess(html.index('class="t">나 양식'), html.index('class="t">가 양식'))

    def test_like_deleted_with_account(self):
        u = _user("del")
        FormLike.objects.create(form=self.a, user=u)
        call_command("delete_account", "del@example.com", verbosity=0, stdout=mock.Mock())
        self.assertFalse(FormLike.objects.exists())

    @override_settings(CACHES=RL_CACHES)
    @mock.patch("config.ratelimit.time.time", return_value=1_800_000_030.0)
    def test_rate_limited(self, _clock):
        caches["ratelimit"].clear()
        self.client.force_login(self.user)
        for _ in range(60):
            self.client.post(self.url())
        r = self.client.post(self.url(), HTTP_HX_REQUEST="true")
        self.assertContains(r, "너무 자주", status_code=429)
        self.assertEqual(self.client.post(self.url()).status_code, 429)
