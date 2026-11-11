"""M4 TV 테마 QA 경계 사례: 다른 학교·동학년·만료 학교는 못 바꾼다, 미리보기(?theme=N)는 저장하지 않는다, 숫자처럼 보이는 문자도 400."""
from config.csp import CSP
from spaces.merge import open_school
from spaces.test_merge import UNTIL

from .models import MealSettings
from .test_tv import TvBase


class ThemeEdgeTests(TvBase):
    def theme(self, space=None):
        return MealSettings.objects.in_space(space or self.hub).get().theme

    def test_other_school_and_grade_space_cannot_set(self):
        far = self.as_(self.far_user)
        self.assertEqual(far.post(f"{self.base}/screens", {"theme": "3"}).status_code, 404)
        self.assertEqual(far.post(f"/s/{self.far.token}/meals/screens", {"theme": "3"}).status_code, 404)
        self.assertEqual(self.theme(), 1)

    def test_each_school_keeps_its_own_theme(self):
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        MealSettings.objects.get_or_create(space=far_hub)
        self.writer.post(f"{self.base}/screens", {"theme": "4"})
        self.assertEqual((self.theme(), self.theme(far_hub)), (4, 1))

    def test_expired_school_cannot_set(self):
        self.expire()
        self.assertEqual(self.writer.post(f"{self.base}/screens", {"theme": "3"}).status_code, 403)
        self.assertEqual(self.theme(), 1)

    def test_digit_like_values_are_400_not_500(self):
        for bad in ("²", "¹", " 3", "3 ", "03x", "1e1"):
            self.assertEqual(self.writer.post(f"{self.base}/screens", {"theme": bad}).status_code, 400, repr(bad))
        self.assertEqual(self.theme(), 1)

    def test_preview_query_does_not_save(self):
        for path in (self.url, f"{self.url}/data", f"{self.base}/screens"):
            client = self.writer if path.startswith("/s/") else self.anon
            self.assertEqual(client.get(path, {"theme": "3"}).status_code, 200)
        self.assertEqual(self.theme(), 1)
        self.assertEqual(self.data()["theme"], 1)

    def test_tv_page_loads_theme_css_under_same_csp(self):
        r = self.anon.get(self.url)
        self.assertContains(r, "css/tv-themes.css")
        self.assertIn("img-src 'self' data: blob:", r["Content-Security-Policy"])
        self.assertEqual(r["Content-Security-Policy"].split("font-src")[1], CSP.split("font-src")[1].replace("frame-ancestors 'none'", "frame-ancestors 'self'"))
