"""V8 도움말 영상: 영상이 있으면 video 태그(controls, 자동 재생 없음, preload none, 포스터), 주제마다 mp4·포스터가 있다."""
import subprocess
import shutil
from unittest import mock, skipUnless

from django.conf import settings
from django.test import Client, TestCase

from . import help as help_page

HELP_DIR = settings.STATICFILES_DIRS[0] / "help"


class HelpVideoTest(TestCase):
    def test_video_tag_has_poster_controls_no_autoplay(self):
        with mock.patch.object(help_page, "_video", lambda key: key == "today"):
            html = Client().get("/help").content.decode()
        self.assertEqual(html.count("<video"), 1)
        tag = html[html.index("<video"):html.index("</video>")]
        self.assertIn("controls", tag)
        self.assertIn('preload="none"', tag)
        self.assertIn('poster="/static/help/today.jpg"', tag)
        self.assertIn('src="/static/help/today.mp4"', tag)
        self.assertNotIn("autoplay", tag)

    def test_every_topic_has_video_and_poster(self):
        for key, *_ in help_page.TOPICS:
            self.assertTrue((HELP_DIR / f"{key}.mp4").is_file(), key)
            self.assertTrue((HELP_DIR / f"{key}.jpg").is_file(), key)
        html = Client().get("/help").content.decode()
        self.assertEqual(html.count("<video"), len(help_page.TOPICS))

    @skipUnless(shutil.which("ffprobe"), "ffprobe 없음")
    def test_videos_short_silent_h264(self):
        for key, *_ in help_page.TOPICS:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration",
                                  "-of", "default=nw=1", str(HELP_DIR / f"{key}.mp4")], capture_output=True, text=True, check=True).stdout
            self.assertIn("codec_name=h264", out, key)
            self.assertNotIn("codec_type=audio", out, key)
            self.assertLessEqual(float(out.split("duration=")[1].split()[0]), 60, key)
            self.assertLess((HELP_DIR / f"{key}.mp4").stat().st_size, 5 * 1024 * 1024, key)
