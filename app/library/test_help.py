"""V7 도움말: /help는 로그인 없이 200, 공간 화면에 진입점, 그림 파일이 다 있음, 영상은 파일이 있을 때만.
seed_help_demo는 DEBUG가 아니면 거부하고, 다시 돌리면 지우고 같은 모습으로 만든다."""
from datetime import date
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings

from collect.models import Collection
from notices.models import Notice
from spaces.management.commands import seed_help_demo as seed
from spaces.models import Member, School, Space
from today.models import Event

from . import help as help_page

User = get_user_model()
HELP_DIR = settings.STATICFILES_DIRS[0] / "help"


class HelpPageTest(TestCase):
    def setUp(self):
        p = [mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=date(2099, 1, 1))
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="새솔초")
        u = User.objects.create(username="google:사일", first_name="사일")
        Member.objects.create(space=self.hub, user=u, name="사일", cls="4-1", group="g4")
        self.c = Client()
        self.c.force_login(u)

    def test_anonymous_sees_all_topics(self):
        with mock.patch.object(help_page, "_video", lambda key: False):  # 영상 파일 유무와 무관하게
            r = Client().get("/help")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for _, title, steps, shots, _ in help_page.TOPICS:
            self.assertIn(title, html)
            self.assertTrue(1 <= len(steps) <= 3 and 1 <= len(shots) <= 3, title)
        self.assertNotIn('class="side"', html)  # 공간 없는 틀
        self.assertNotIn("<video", html)

    def test_member_gets_shell_with_help_current(self):
        r = self.c.get("/help")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<a href="/help" aria-current="page">')
        self.assertContains(r, 'class="side"')

    def test_entry_point_on_space_pages(self):
        for path in ("today", "schedule", "c/", "settings"):
            r = self.c.get(f"/s/{self.hub.token}/{path}")
            self.assertEqual(r.status_code, 200, path)
            self.assertContains(r, 'href="/help"', msg_prefix=path)

    def test_every_shot_file_exists(self):
        for _, _, _, shots, _ in help_page.TOPICS:
            for name, alt in shots:
                self.assertTrue((HELP_DIR / f"{name}.png").is_file(), name)
                self.assertTrue(alt)

    def test_video_only_when_file_exists(self):
        with mock.patch.object(help_page, "_video", lambda key: key == "today"):
            r = Client().get("/help")
        self.assertContains(r, "<video", count=1)
        self.assertContains(r, "help/today.mp4")


class SeedHelpDemoTest(TestCase):
    @override_settings(DEBUG=False)
    def test_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_help_demo", stdout=mock.Mock())
        self.assertFalse(School.objects.filter(name=seed.NAME).exists())

    @override_settings(DEBUG=True)
    def test_rerun_rebuilds_same_school(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초")
        call_command("seed_help_demo", stdout=mock.Mock())
        def counts():
            return tuple(m.objects.filter(space__school__name=seed.NAME).count() for m in (Member, Event, Collection, Notice))

        first = counts()
        call_command("seed_help_demo", stdout=mock.Mock())
        self.assertEqual(counts(), first)
        self.assertEqual(first[0], len(seed.PEOPLE))
        self.assertEqual(School.objects.filter(name=seed.NAME).count(), 1)
        self.assertTrue(School.objects.filter(pk=other.pk).exists())  # 다른 학교는 그대로
        self.assertEqual(User.objects.filter(username__in=seed.LOGIN.values()).count(), len(seed.PEOPLE))
        admin = User.objects.get(username=seed.LOGIN["admin"])
        self.assertTrue(admin.check_password(seed.PASSWORD))
