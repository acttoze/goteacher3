"""A6 개인정보처리방침·이용약관 게시본, B6 동학년 화면의 새로 모으기 링크."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from spaces.models import Member, School, Space


class LegalPageTests(TestCase):
    def test_public_pages_filled(self):
        for url in ("/privacy", "/terms"):
            r = Client().get(url)
            self.assertEqual(r.status_code, 200, url)
            body = r.content.decode()
            for want in ("정이점", "admin@goteacher.xyz", "2022년 3월 1일"):
                self.assertIn(want, body, url)
            for bad in ('class="blank"', 'class="draft"', "초안", "[", "티로드"):
                self.assertNotIn(bad, body, url)

    def test_privacy_keeps_retention_and_processor(self):
        body = Client().get("/privacy").content.decode()
        self.assertIn("Oracle(Oracle Cloud Infrastructure, 춘천 리전)", body)
        self.assertIn("마감 후 1년", body)
        self.assertIn("(7일)", body)

    def test_home_links_legal(self):
        body = Client().get("/").content.decode()
        self.assertIn('href="/privacy"', body)
        self.assertIn('href="/terms"', body)


class GradeNewCollectLinkTests(TestCase):
    def test_grade_has_new_collect_link(self):
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2"])
        p.start()
        self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        space = Space.objects.create(school=school, grade=4, name="새솔초 4학년")
        u = get_user_model().objects.create(username="google:1", first_name="송민재")
        Member.objects.create(space=space, user=u, name="송민재", cls="4-1")
        self.client.force_login(u)
        r = self.client.get(f"/s/{space.token}/")
        self.assertContains(r, f'href="/s/{space.token}/c/new"')
        self.assertContains(r, "새로 모으기")
