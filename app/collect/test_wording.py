"""T-003 쪽지 문구에 시간 약속(30초)을 넣지 않는다: 취합 쪽지(복사 문구·만들기 미리보기)와 동학년 초대 문구."""
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from spaces import views as space_views

from . import views

NEW = "로그인 없이 링크에서 바로 낼 수 있어요."


class CollectMessageWordingTest(SimpleTestCase):
    def test_copied_message(self):
        c = SimpleNamespace(title="체험학습 인원", link_token="tok", created_by_id=1,
                            fields=SimpleNamespace(all=lambda: [SimpleNamespace(name="인원")]))
        req = SimpleNamespace(member=SimpleNamespace(pk=1, name="사일", cls="4-1"), space=SimpleNamespace(is_school=False))
        msg = views._link_ctx(req, c)["msg"]
        self.assertIn(f"우리 반 칸만 채워 주세요. {NEW}", msg)
        self.assertNotIn("30초", msg)

    def test_new_screen_preview(self):
        src = (Path(views.__file__).parent / "templates/collect/new.html").read_text()
        self.assertIn(f"만 채워 주세요. {NEW}", src)
        self.assertNotIn("30초", src)


class GradeInviteWordingTest(SimpleTestCase):
    def test_no_time_promise(self):
        space = SimpleNamespace(is_org=False, is_school=False, invite_token="tok", grade=4,
                                school=SimpleNamespace(name="새솔초"))
        msg = space_views._invite(space, SimpleNamespace(name="사일", cls_text="4학년 1반"))["msg"]
        self.assertIn("반·이름만 고르면 들어와요. (가입 없음)\n", msg)
        self.assertNotIn("30초", msg)
