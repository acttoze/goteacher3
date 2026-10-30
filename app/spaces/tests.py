"""B3 공간 격리 불변식: 다른 공간이면 404, 같은 공간이면 200. B4부터 공간 주소는 소속 회원만."""
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path

from accounts.models import DEVICE_COOKIE, DeviceToken

from .models import Member, School, Space, get_in_space_or_404


def member_detail(request, member_key):
    m = get_in_space_or_404(Member, request, key=member_key)
    return HttpResponse(m.name)


def member_list(request):
    names = Member.objects.in_space(request.space).values_list("name", flat=True)
    return HttpResponse(",".join(sorted(names)))


urlpatterns = [
    path("s/<str:space_token>/members/", member_list),
    path("s/<str:space_token>/members/<str:member_key>/", member_detail),
]


@override_settings(ROOT_URLCONF=__name__)
class SpaceIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua = User.objects.create(username="google:a")
        cls.ub = User.objects.create(username="google:b")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.mb = Member.objects.create(space=cls.b, name="이나래", cls="5-1", user=cls.ub)

    def setUp(self):
        self.client.force_login(self.ua)

    def test_same_space_member_200(self):
        r = self.client.get(f"/s/{self.a.token}/members/{self.ma.key}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode(), "김가람")

    def test_other_space_member_404(self):
        r = self.client.get(f"/s/{self.a.token}/members/{self.mb.key}/")
        self.assertEqual(r.status_code, 404)

    def test_list_only_own_space(self):
        r = self.client.get(f"/s/{self.a.token}/members/")
        self.assertEqual(r.content.decode(), "김가람")

    def test_unknown_space_token_404(self):
        r = self.client.get(f"/s/{'x' * 32}/members/")
        self.assertEqual(r.status_code, 404)

    def test_sequential_id_not_accepted(self):
        r = self.client.get(f"/s/{self.a.pk}/members/{self.ma.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_tokens_are_long_and_unique(self):
        self.assertGreaterEqual(len(self.a.token), 32)
        self.assertGreaterEqual(len(self.a.invite_token), 32)  # token_urlsafe(24) = 32자
        self.assertGreaterEqual(len(self.ma.key), 32)
        self.assertNotEqual(self.a.token, self.b.token)
        self.assertNotEqual(self.a.token, self.a.invite_token)

    def test_in_space_requires_space(self):
        with self.assertRaises(ValueError):
            Member.objects.in_space(None)


def no_space_member(request, member_key):
    m = get_in_space_or_404(Member, request, key=member_key)
    return HttpResponse(m.name)


urlpatterns += [path("nospace/members/<str:member_key>/", no_space_member)]


@override_settings(ROOT_URLCONF=__name__)
class SpaceIsolationEdgeTests(TestCase):
    """QA 추가: 다른 학교, 토큰 맞바꾸기, 공간 없는 주소."""

    @classmethod
    def setUpTestData(cls):
        s1 = School.objects.create(neis_atpt="S10", neis_code="1111111", name="가초")
        s2 = School.objects.create(neis_atpt="S10", neis_code="2222222", name="나초")
        cls.a = Space.objects.create(school=s1, grade=4, name="4학년")
        cls.c = Space.objects.create(school=s2, grade=4, name="4학년")  # 같은 학년, 다른 학교
        User = get_user_model()
        cls.ua = User.objects.create(username="google:a")
        cls.uc = User.objects.create(username="google:c")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.mc = Member.objects.create(space=cls.c, name="박다솜", cls="4-1", user=cls.uc)

    def test_other_school_same_grade_404_both_ways(self):
        self.client.force_login(self.ua)
        r1 = self.client.get(f"/s/{self.a.token}/members/{self.mc.key}/")
        self.client.force_login(self.uc)
        r2 = self.client.get(f"/s/{self.c.token}/members/{self.ma.key}/")
        self.assertEqual((r1.status_code, r2.status_code), (404, 404))

    def test_other_school_list_isolated(self):
        self.client.force_login(self.uc)
        r = self.client.get(f"/s/{self.c.token}/members/")
        self.assertEqual(r.content.decode(), "박다솜")

    def test_member_key_as_space_token_404(self):
        self.client.force_login(self.ua)
        # 멤버 키를 공간 토큰 자리에 끼워 넣어도 통하지 않는다
        r = self.client.get(f"/s/{self.ma.key}/members/{self.ma.key}/")
        self.assertEqual(r.status_code, 404)

    def test_token_prefix_or_case_change_404(self):
        self.client.force_login(self.ua)
        t = self.a.token
        for bad in (t[:-1], t + "x", t.swapcase()):
            if bad == t:
                continue
            r = self.client.get(f"/s/{bad}/members/")
            self.assertEqual(r.status_code, 404, bad)

    def test_route_without_space_fails_closed(self):
        # 공간 토큰이 없는 주소에서 공간 소속 조회를 하면 데이터를 내주지 않고 막힌다
        with self.assertRaises(ValueError):
            self.client.get(f"/nospace/members/{self.ma.key}/")


@override_settings(ROOT_URLCONF=__name__)
class SpaceMembershipTests(TestCase):
    """B4 소속 확인: 공간 주소는 로그인·기기 토큰으로 식별된 그 공간 회원만 통과."""

    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="3333333", name="다초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua = User.objects.create(username="google:a")
        cls.ub = User.objects.create(username="google:b")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.mb = Member.objects.create(space=cls.b, name="이나래", cls="5-1", user=cls.ub)
        cls.loose = Member.objects.create(space=cls.a, name="이름만", cls="4-2")  # 로그인 없는 회원(B7)

    def test_anonymous_with_valid_link_is_sent_to_login_not_inside(self):
        r = self.client.get(f"/s/{self.a.token}/members/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], f"/?next=/s/{self.a.token}/members/")

    def test_anonymous_unknown_token_same_answer(self):
        # 토큰이 있는지 없는지 익명에게 알려 주지 않는다
        r = self.client.get(f"/s/{'x' * 32}/members/")
        self.assertEqual(r.status_code, 302)

    def test_logged_in_non_member_404(self):
        self.client.force_login(self.ub)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/members/").status_code, 404)

    def test_user_without_any_member_404(self):
        self.client.force_login(get_user_model().objects.create(username="google:z"))
        self.assertEqual(self.client.get(f"/s/{self.a.token}/members/").status_code, 404)

    def test_invite_token_is_not_a_space_address(self):
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(f"/s/{self.a.invite_token}/members/").status_code, 404)

    def test_device_cookie_only_member_200_own_404_other(self):
        self.client.cookies[DEVICE_COOKIE] = DeviceToken.issue(self.ua)
        r = self.client.get(f"/s/{self.a.token}/members/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"/s/{self.b.token}/members/").status_code, 404)

    def test_forged_device_cookie_not_inside(self):
        DeviceToken.issue(self.ua)
        self.client.cookies[DEVICE_COOKIE] = "y" * 43
        self.assertEqual(self.client.get(f"/s/{self.a.token}/members/").status_code, 302)

    def test_request_member_is_mine(self):
        self.client.force_login(self.ua)
        r = self.client.get(f"/s/{self.a.token}/me/")
        self.assertEqual(r.content.decode(), self.ma.key)


    # QA 추가(B4)
    def test_anonymous_invite_token_same_redirect(self):
        r = self.client.get(f"/s/{self.a.invite_token}/members/")
        self.assertEqual(r.status_code, 302)

    def test_deactivated_member_link_404(self):
        # 회원 연결이 끊기면(user=None) 같은 사용자라도 들어오지 못한다
        Member.objects.filter(pk=self.ma.pk).update(user=None)
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/members/").status_code, 404)

    def test_logout_then_space_redirects(self):
        raw = DeviceToken.issue(self.ua)
        self.client.cookies[DEVICE_COOKIE] = raw
        self.assertEqual(self.client.get(f"/s/{self.a.token}/members/").status_code, 200)
        self.client.post("/auth/logout")
        self.client.cookies[DEVICE_COOKIE] = raw
        self.assertEqual(self.client.get(f"/s/{self.a.token}/members/").status_code, 302)


def me(request):
    return HttpResponse(request.member.key)


urlpatterns += [path("s/<str:space_token>/me/", me)]

from accounts.views import logout_view  # noqa: E402  QA: 로그아웃 뒤 공간 주소 확인용

urlpatterns += [path("auth/logout", logout_view)]
