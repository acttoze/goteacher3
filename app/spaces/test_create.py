"""B6 동학년 만들기: 학교 찾기 → 학년·반 → 생성 → 동학년 화면. NEIS는 목."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Member, School, Space

User = get_user_model()

SCHOOLS = [
    {"name": "새솔초등학교", "atpt": "J10", "code": "7531234", "office": "경기도교육청", "address": "경기도 화성시 새솔동"},
    {"name": "서울새솔초등학교", "atpt": "B10", "code": "7010001", "office": "서울특별시교육청", "address": "서울 강서구"},
]


def neis_mock(schools=SCHOOLS, total=None, classes=("1", "2", "3", "4", "5")):
    return [
        mock.patch("spaces.neis.search_schools", return_value=(list(schools), total if total is not None else len(schools))),
        mock.patch("spaces.neis.class_names", return_value=list(classes)),
    ]


class NeisMocked(TestCase):
    patches = None

    def setUp(self):
        self.mocks = [p.start() for p in (self.patches or neis_mock())]
        self.addCleanup(mock.patch.stopall)
        self.user = User.objects.create(username="google:1", first_name="송민재")

    def make(self, school_i=0, grade=4, cls="4-1", name="송민재"):
        self.client.get("/", {"q": "새솔"})
        self.client.post("/new/school", {"i": school_i})
        return self.client.post("/new/class", {"grade": grade, "cls": cls, "name": name})

    def space_url(self, **kw):
        """만들면 4단계 확인 화면(/made)으로 간다(B7). 동학년 주소만 돌려준다."""
        loc = self.make(**kw)["Location"]
        self.assertTrue(loc.endswith("/made"), loc)
        return loc.removesuffix("made")


class LoginRequiredTests(NeisMocked):
    def test_anonymous_home_is_google_start_no_search(self):
        r = self.client.get("/", {"q": "새솔"})
        self.assertContains(r, "구글 계정으로 시작하기")
        self.mocks[0].assert_not_called()

    def test_anonymous_cannot_pick_or_create(self):
        r = self.client.post("/new/school", {"i": 0})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/?next="))
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/?next="))
        self.assertEqual((School.objects.count(), Space.objects.count(), Member.objects.count()), (0, 0, 0))


class CreateFlowTests(NeisMocked):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_search_shows_schools(self):
        r = self.client.get("/", {"q": "새솔"})
        self.assertContains(r, "새솔초등학교")
        self.assertContains(r, "2곳을 찾았어요")
        self.assertContains(r, "4단계 중 2단계")

    def test_search_htmx_returns_fragment(self):
        r = self.client.get("/", {"q": "새솔"}, headers={"HX-Request": "true"})
        self.assertContains(r, "서울새솔초등학교")
        self.assertNotContains(r, "<html")

    def test_search_limit_notice(self):
        mock.patch.stopall()
        for p in neis_mock(total=37):
            p.start()
        r = self.client.get("/", {"q": "새솔"})
        self.assertContains(r, "37곳 중 2곳만 보여요")

    def test_search_too_short_and_empty(self):
        self.assertContains(self.client.get("/", {"q": "새"}), "두 글자 이상")
        self.mocks[0].return_value = ([], 0)
        self.assertContains(self.client.get("/", {"q": "없는학교"}), "「없는학교」로 찾은 학교가 없어요")

    def test_search_escapes_query(self):
        self.mocks[0].return_value = ([], 0)
        r = self.client.get("/", {"q": "<script>x</script>"})
        self.assertNotContains(r, "<script>x</script>")

    def test_pick_uses_server_hits_only(self):
        self.client.get("/", {"q": "새솔"})
        self.assertEqual(self.client.post("/new/school", {"i": 9}).status_code, 302)
        self.assertEqual(self.client.get("/new/class")["Location"], "/")  # 고른 학교 없음
        self.client.post("/new/school", {"i": 1})
        r = self.client.get("/new/class")
        self.assertContains(r, "서울새솔초등학교")
        self.assertContains(r, "나이스 학급 정보: 4학년 5개 반")
        self.assertContains(r, "전담")
        self.assertContains(r, 'value="송민재"')  # 구글 이름을 가져온다

    def test_grade_switch_htmx_keeps_name(self):
        self.client.get("/", {"q": "새솔"})
        self.client.post("/new/school", {"i": 0})
        r = self.client.get("/new/class", {"grade": 6, "name": "김가람"}, headers={"HX-Request": "true"})
        self.assertContains(r, "6학년 동학년 만들기")
        self.assertContains(r, 'value="김가람"')
        self.assertContains(r, 'data-v="6-5"')
        self.assertNotContains(r, "<html")
        self.mocks[1].assert_called_with("J10", "7531234", 6)

    def test_create_makes_school_space_member_and_redirects(self):
        r = self.make()
        space = Space.objects.get()
        self.assertEqual(r["Location"], f"/s/{space.token}/made")
        self.assertEqual((space.school.neis_code, space.school.name), ("7531234", "새솔초등학교"))
        self.assertEqual((space.grade, space.name), (4, "새솔초 4학년"))
        m = Member.objects.in_space(space).get()
        self.assertEqual((m.user, m.name, m.cls), (self.user, "송민재", "4-1"))
        self.assertNotIn("new_school", self.client.session)

    def test_space_page_after_create(self):
        r = self.client.get(self.space_url())
        self.assertContains(r, "새솔초 4학년")
        self.assertContains(r, "송민재 (나)")
        self.assertContains(r, "구글 가입 · 만든 사람")
        self.assertContains(r, "1</b><span class=\"num\">/ 6칸")  # 5반 + 전담
        self.assertContains(r, "아직이에요", count=5)
        self.assertNotContains(r, "티로드")

    def test_home_goes_to_my_space(self):
        loc = self.space_url()
        self.assertEqual(self.client.get("/")["Location"], loc)

    def test_school_reused_by_neis_code(self):
        self.make()
        other = User.objects.create(username="google:2", first_name="정하은")
        self.client.force_login(other)
        self.make(grade=5, cls="5-2", name="정하은")
        self.assertEqual(School.objects.count(), 1)
        self.assertEqual(Space.objects.count(), 2)

    def test_same_school_grade_duplicate_allowed(self):
        # prototype: 막지 않고 "초대 링크로 들어오세요" 안내만. 가장 단순하게 중복 허용(리드 확인 대기).
        self.make()
        other = User.objects.create(username="google:3")
        self.client.force_login(other)
        self.make(cls="4-2", name="김도윤")
        self.assertEqual(Space.objects.filter(grade=4).count(), 2)

    def test_validation_errors(self):
        r = self.make(cls="")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "반을 하나 골라 주세요", status_code=400)
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-9", "name": "x"})  # NEIS에 없는 반
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "  "})
        self.assertContains(r, "이름을 써 주세요", status_code=400)
        self.assertEqual(Space.objects.count(), 0)

    def test_bad_grade_falls_back(self):
        r = self.make(grade=9, cls="9-1")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Space.objects.count(), 0)

    def test_neis_classes_missing_fallback_four(self):
        self.mocks[1].return_value = []
        self.client.get("/", {"q": "새솔"})
        self.client.post("/new/school", {"i": 0})
        r = self.client.get("/new/class")
        self.assertContains(r, 'data-v="4-4"')
        self.assertNotContains(r, 'data-v="4-5"')
        self.assertContains(r, "4개 반으로 보여요")

    def test_new_school_name_from_server_not_client(self):
        self.client.get("/", {"q": "새솔"})
        self.client.post("/new/school", {"i": 0, "name": "가짜초", "code": "0000000"})
        self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "송민재", "school": "가짜초"})
        self.assertEqual(School.objects.get().name, "새솔초등학교")


class SpaceIsolationB6Tests(NeisMocked):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = self.space_url()

    def test_other_user_404(self):
        self.client.force_login(User.objects.create(username="google:x"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_other_space_member_404_and_list_isolated(self):
        other = User.objects.create(username="google:y", first_name="이나래")
        self.client.force_login(other)
        other_url = self.space_url(grade=5, cls="5-1", name="이나래")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        r = self.client.get(other_url)
        self.assertContains(r, "이나래")
        self.assertNotContains(r, "송민재")

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        r = self.client.get(self.url)
        self.assertEqual(r["Location"], f"/?next={self.url}")

    def test_space_url_is_token_not_id(self):
        space = Space.objects.get()
        self.assertGreaterEqual(len(space.token), 32)
        self.assertEqual(self.client.get(f"/s/{space.pk}/").status_code, 404)


class QaB6EdgeTests(NeisMocked):
    """QA 추가: 구현 테스트에 없던 경계 사례."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_invite_token_is_not_space_address(self):
        # 토큰 바꿔 끼우기: 회원이라도 초대 토큰으로는 공간 주소가 열리지 않는다.
        self.make()
        space = Space.objects.get()
        self.assertEqual(self.client.get(f"/s/{space.invite_token}/").status_code, 404)
        self.assertEqual(self.client.get(f"/s/{space.token}x/").status_code, 404)

    def test_create_requires_csrf(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        c.get("/", {"q": "새솔"})
        self.assertEqual(c.post("/new/school", {"i": 0}).status_code, 403)
        self.assertEqual(c.post("/new/class", {"grade": 4, "cls": "4-1", "name": "x"}).status_code, 403)
        self.assertEqual(Space.objects.count(), 0)

    def test_pick_without_search_or_bad_index(self):
        self.assertEqual(self.client.post("/new/school", {"i": 0})["Location"], "/")
        self.client.get("/", {"q": "새솔"})
        self.assertEqual(self.client.post("/new/school", {"i": "abc"})["Location"], "/")
        self.assertEqual(self.client.get("/new/school").status_code, 405)

    def test_space_page_when_neis_classes_missing(self):
        url = self.space_url()
        self.mocks[1].return_value = []  # 동학년 화면에서 NEIS 학급 조회 실패
        r = self.client.get(url)
        self.assertContains(r, "송민재 (나)")
        self.assertNotContains(r, "아직이에요")
        self.assertContains(r, "1</b><span class=\"num\">/ 1칸")

    def test_space_page_member_outside_neis_slots(self):
        # 만들 때 반(4-5)이 이후 NEIS 학급(3개 반)에 없으면 칸을 하나 더한다.
        url = self.space_url(cls="4-5")
        self.mocks[1].return_value = ["1", "2", "3"]
        r = self.client.get(url)
        self.assertContains(r, "1</b><span class=\"num\">/ 5칸")  # 3반 + 전담 + 칸 밖 1
        self.assertContains(r, "아직이에요", count=4)


class HardeningB6Tests(NeisMocked):
    """B6 후속: 교사 한 명은 동학년 하나, 인덱스 경계, 이름 정리."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_member_cannot_create_second_space(self):
        url = self.space_url()
        self.client.get("/", {"q": "새솔"})  # 회원이면 홈은 내 동학년으로 보낸다
        session = self.client.session
        session["new_school"] = SCHOOLS[1]
        session.save()
        r = self.client.post("/new/class", {"grade": 5, "cls": "5-1", "name": "송민재"})
        self.assertEqual(r["Location"], url)
        self.assertEqual(Space.objects.count(), 1)
        self.assertNotIn("new_school", self.client.session)
        self.assertEqual(self.client.post("/new/school", {"i": 0})["Location"], url)

    def test_double_submit_makes_one_space(self):
        loc = self.space_url()
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "송민재"})
        self.assertEqual(r["Location"], loc)
        self.assertEqual((Space.objects.count(), Member.objects.count()), (1, 1))

    def test_validation_error_keeps_picked_school(self):
        self.client.get("/", {"q": "새솔"})
        self.client.post("/new/school", {"i": 0})
        self.assertEqual(self.client.post("/new/class", {"grade": 4, "cls": ""}).status_code, 400)
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "송민재"})
        self.assertTrue(r["Location"].startswith("/s/"))

    def test_pick_rejects_negative_and_out_of_range(self):
        self.client.get("/", {"q": "새솔"})
        for i in (-1, -2, 2, 99):
            self.assertEqual(self.client.post("/new/school", {"i": i})["Location"], "/")
            self.assertNotIn("new_school", self.client.session)

    def test_name_cleaned(self):
        dirty = "‮ 송​민\u0007재⁦ ﻿"
        self.make(name=dirty)
        self.assertEqual(Member.objects.get().name, "송민재")

    def test_name_only_invisible_rejected(self):
        r = self.make(name=" ​‮⁠ ")
        self.assertContains(r, "이름을 써 주세요", status_code=400)
        self.assertEqual(Space.objects.count(), 0)
