"""B3 공간 격리 불변식: 다른 공간이면 404, 같은 공간이면 200."""
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path

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
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1")
        cls.mb = Member.objects.create(space=cls.b, name="이나래", cls="5-1")

    def test_same_space_member_200(self):
        r = self.client.get(f"/s/{self.a.invite_token}/members/{self.ma.key}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode(), "김가람")

    def test_other_space_member_404(self):
        r = self.client.get(f"/s/{self.a.invite_token}/members/{self.mb.key}/")
        self.assertEqual(r.status_code, 404)

    def test_list_only_own_space(self):
        r = self.client.get(f"/s/{self.a.invite_token}/members/")
        self.assertEqual(r.content.decode(), "김가람")

    def test_unknown_space_token_404(self):
        r = self.client.get(f"/s/{'x' * 32}/members/")
        self.assertEqual(r.status_code, 404)

    def test_sequential_id_not_accepted(self):
        r = self.client.get(f"/s/{self.a.pk}/members/{self.ma.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_tokens_are_long_and_unique(self):
        self.assertGreaterEqual(len(self.a.invite_token), 32)  # token_urlsafe(24) = 32자
        self.assertGreaterEqual(len(self.ma.key), 32)
        self.assertNotEqual(self.a.invite_token, self.b.invite_token)

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
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1")
        cls.mc = Member.objects.create(space=cls.c, name="박다솜", cls="4-1")

    def test_other_school_same_grade_404_both_ways(self):
        r1 = self.client.get(f"/s/{self.a.invite_token}/members/{self.mc.key}/")
        r2 = self.client.get(f"/s/{self.c.invite_token}/members/{self.ma.key}/")
        self.assertEqual((r1.status_code, r2.status_code), (404, 404))

    def test_other_school_list_isolated(self):
        r = self.client.get(f"/s/{self.c.invite_token}/members/")
        self.assertEqual(r.content.decode(), "박다솜")

    def test_member_key_as_space_token_404(self):
        # 멤버 키를 공간 토큰 자리에 끼워 넣어도 통하지 않는다
        r = self.client.get(f"/s/{self.ma.key}/members/{self.ma.key}/")
        self.assertEqual(r.status_code, 404)

    def test_token_prefix_or_case_change_404(self):
        t = self.a.invite_token
        for bad in (t[:-1], t + "x", t.swapcase()):
            if bad == t:
                continue
            r = self.client.get(f"/s/{bad}/members/")
            self.assertEqual(r.status_code, 404, bad)

    def test_route_without_space_fails_closed(self):
        # 공간 토큰이 없는 주소에서 공간 소속 조회를 하면 데이터를 내주지 않고 막힌다
        with self.assertRaises(ValueError):
            self.client.get(f"/nospace/members/{self.ma.key}/")
