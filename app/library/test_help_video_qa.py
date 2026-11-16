"""V8 QA 보강: 재생성 스크립트(record.py)가 도움말 주제를 빠짐없이 같은 순서로 녹화하는지,
녹화에 쓰는 이름·아이디가 가상 학교(seed_help_demo) 것뿐인지, mp4가 Range 요청에 206으로 나가는지(사파리·중간 탐색).
record.py·capture.py는 플레이라이트를 불러오므로 import하지 않고 소스를 읽어 본다."""
import ast
import io

from django.conf import settings
from django.test import SimpleTestCase
from whitenoise import WhiteNoise

from spaces.management.commands import seed_help_demo as seed

from . import help as help_page

OPS = settings.BASE_DIR.parent / "ops" / "help"
STATIC = settings.STATICFILES_DIRS[0]


def _tree(name):
    return ast.parse((OPS / name).read_text(encoding="utf-8"))


def _calls(tree, attr):
    """st.<attr>("문자열", ...) 호출의 첫 인자들(순서대로)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for c in ast.walk(node):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == attr
                        and c.args and isinstance(c.args[0], ast.Constant)):
                    out.append((node.name, c.args[0].value))
    return out


class RecordScriptTest(SimpleTestCase):
    def test_videos_cover_every_topic_in_order(self):
        tree = _tree("record.py")
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        order = next(n.value.elts for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "VIDEOS")
        opened = []
        for ref in order:
            names = [v for f, v in _calls(funcs[ref.id], "open") if f == ref.id]
            self.assertEqual(len(names), 1, ref.id)  # 영상 하나 = open 한 번
            opened.append(names[0])
        self.assertEqual(opened, [k for k, *_ in help_page.TOPICS])

    def test_names_typed_on_camera_are_demo_people(self):
        people = {name for _, name, *_ in seed.PEOPLE}
        tree = _tree("record.py")
        send = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "video_collect_send")
        names = [c.args[1].value for c in ast.walk(send) if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", "") == "type" and c.args[0].value == "#findQ"]
        loop = next(n for n in ast.walk(send) if isinstance(n, ast.For))  # 녹화 밖에서 더 내는 사람들
        names += [t.elts[0].value for t in loop.iter.elts]
        self.assertEqual(len(names), 3)
        self.assertTrue(set(names) <= people, names)

    def test_capture_login_matches_seed(self):
        tree = _tree("capture.py")
        login = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "LOGIN")
        password = next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "PASSWORD")
        for who, username in login.items():
            self.assertEqual(seed.LOGIN[who], username, who)
        self.assertEqual(password, seed.PASSWORD)
        site = next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "SITE_URL")
        self.assertNotIn("127.0.0.1", site)


class VideoRangeTest(SimpleTestCase):
    """whitenoise(DEBUG=0 서빙)가 mp4를 Range 요청에 206으로 준다. 운영과 같은 라이브러리·파일로 본다."""

    def _get(self, path, **headers):
        app = WhiteNoise(lambda env, start: [b""], root=str(STATIC), prefix="static/")
        got = {}
        env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/static/" + path, "wsgi.input": io.BytesIO(), **headers}
        body = b"".join(app(env, lambda status, hs, *a: got.update(status=status, headers=dict(hs))))
        return got["status"], got["headers"], body

    def test_mp4_range_206(self):
        for key, *_ in help_page.TOPICS:
            size = (STATIC / "help" / f"{key}.mp4").stat().st_size
            status, headers, body = self._get(f"help/{key}.mp4", HTTP_RANGE="bytes=0-1")
            self.assertTrue(status.startswith("206"), (key, status))
            self.assertEqual(headers["Content-Range"], f"bytes 0-1/{size}")
            self.assertEqual(headers["Content-Type"], "video/mp4")
            self.assertEqual(len(body), 2)
            status, headers, _ = self._get(f"help/{key}.mp4", HTTP_RANGE=f"bytes={size // 2}-")
            self.assertTrue(status.startswith("206"), (key, status))
