"""V7 QA 보강: 구현 테스트(test_help)에 없는 경계.
- 도움말 글에 따옴표로 적은 단추·탭 이름이 지금 화면(템플릿·스크립트)에 있다(글과 화면이 어긋나면 실패)
- help 폴더의 PNG는 모두 도움말에서 쓰고, 1536×750을 넘지 않는다(남는 그림·잘못 찍힌 그림 방지)
- /help는 읽기만(POST 405), 로그인 전 화면에 공간 주소가 없고 '티로드'가 없다, 회원 화면은 캐시하지 않는다
"""
import re
from datetime import date
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from spaces.models import Member, School, Space

from . import help as help_page

APP = Path(settings.BASE_DIR)
STATIC = settings.STATICFILES_DIRS[0]
OUTSIDE = {"새 고티처 이용해 보기"}  # 구 고티처 사이트의 단추(이 저장소 밖)


def _screen_text():
    # 탭 이름 일부는 views.py에 있다(예: 취합 만들기의 '한글 양식 올리기'). 도움말 자신(help.py)은 뺀다
    files = [p for p in APP.rglob("*") if p.suffix in (".html", ".js", ".py") and "test" not in p.name
             and p.name != "help.py" and "migrations" not in p.parts]
    files += list(STATIC.rglob("*.js"))
    return "\n".join(p.read_text(errors="ignore") for p in files)


class HelpWordsMatchScreenTest(TestCase):
    def test_quoted_labels_exist_on_screen(self):
        text = _screen_text()
        missing = []
        for _, title, steps, _, _ in help_page.TOPICS:
            for s in steps:
                for label in re.findall(r"'([^']+)'", s):
                    if label not in OUTSIDE and label not in text:
                        missing.append((title, label))
        self.assertEqual(missing, [])


class HelpShotFilesTest(TestCase):
    def test_no_orphan_png_and_size_within_screen(self):
        from PIL import Image

        used = {name for *_, shots, _ in help_page.TOPICS for name, _ in shots}
        on_disk = {p.stem for p in (STATIC / "help").glob("*.png")}
        self.assertEqual(on_disk - used, set(), "도움말에서 안 쓰는 그림")
        for name in used:
            w, h = Image.open(STATIC / "help" / f"{name}.png").size
            self.assertTrue(w <= 1536 and h <= 750, (name, w, h))


class HelpPageEdgeTest(TestCase):
    def test_post_not_allowed(self):
        self.assertEqual(Client().post("/help").status_code, 405)

    def test_anonymous_has_no_space_link_and_no_old_name(self):
        html = Client().get("/help").content.decode()
        self.assertNotIn("/s/", html)
        self.assertNotIn("티로드", html)
        self.assertIn('href="/start"', html)

    def test_member_page_not_cached_and_anonymous_page_cacheable(self):
        p = [mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=date(2099, 1, 1))
        hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        u = get_user_model().objects.create(username="google:사일", first_name="사일")
        Member.objects.create(space=hub, user=u, name="사일", cls="4-1", group="g4")
        c = Client()
        c.force_login(u)
        r = c.get("/help")
        self.assertIn("no-cache", r.get("Cache-Control", ""))
        self.assertContains(r, f"/s/{hub.token}/")
        self.assertNotIn("no-cache", Client().get("/help").get("Cache-Control", ""))
