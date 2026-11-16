"""V6 QA 보강: 같은 학교 다른 공간·토큰 바꿔 끼우기, 의견함 이스케이프·나간 자리·쪽 넘김·비활성 운영자, 관리 명령 잘못된 값.

tests.py와 겹치지 않는 경계만 둔다.
"""
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import Client

from legacy.models import LegacyMap
from spaces.models import Space

from .models import Feedback
from .tests import HX, ON, Base
from .views import PAGE

User = get_user_model()


class IsolationQATests(Base):
    def setUp(self):
        super().setUp()
        LegacyMap.objects.create(kind="school", legacy_id=self.school.neis_code, obj_id=self.school.pk)
        self.g4 = Space.objects.create(school=self.school, grade=4, name="새솔초 4학년")  # 같은 학교, 다른 공간
        self.same_school = self.seat("4-2", "같은학교", "", space=self.g4)

    @ON
    def test_same_school_other_space_member_404(self):
        c = self.as_(self.same_school)
        self.assertEqual(c.post(f"{self.base}/feedback", {"text": "x"}, headers=HX).status_code, 404)
        self.assertEqual(c.post(f"{self.base}/feedback/back", {"kind": "back_slow"}, headers=HX).status_code, 404)
        self.assertFalse(Feedback.objects.exists())

    @ON
    def test_token_swap_back_404(self):
        """다른 학교 회원이 이관 학교 토큰으로 바꿔 끼워도 404, 저장 없음."""
        r = self.as_(self.far).post(f"{self.base}/feedback/back", {"kind": "back_slow"}, headers=HX)
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Feedback.objects.exists())

    def test_anon_back_not_saved(self):
        r = Client().post(f"{self.base}/feedback/back", {"kind": "back_slow"})
        self.assertIn(r.status_code, (302, 403, 404))
        self.assertFalse(Feedback.objects.exists())

    @ON
    def test_band_mode_has_single_feedback_button(self):
        """띠가 있으면 머리줄 단추는 빠진다(같은 단추 두 개 없음). 손님은 돌아가기 대화상자도 없다."""
        html = self.as_(self.t41).get(f"{self.base}/today").content.decode()
        self.assertEqual(html.count('data-open="#fbDlg"'), 1)
        self.assertNotContains(self.as_(self.guest).get(f"{self.base}/today"), 'id="backDlg"')


class InboxQATests(Base):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create(username="google:ops2", is_staff=True)

    def test_text_escaped(self):
        self.send(self.t41, text='<script>alert(1)</script><img src=x onerror=1>')
        r = self.as_(user=self.staff).get("/ops/feedback")
        self.assertNotContains(r, "<script>alert(1)")
        self.assertNotContains(r, "<img src=x")
        self.assertContains(r, "&lt;script&gt;alert(1)")

    def test_member_left_kept_as_left(self):
        self.send(self.t41, text="나가기 전 의견")
        self.t41.delete()
        r = self.as_(user=self.staff).get("/ops/feedback")
        self.assertContains(r, "나가기 전 의견")
        self.assertContains(r, "나간 선생님")
        self.assertNotContains(r, "사일")

    def test_inactive_staff_404(self):
        self.staff.is_active = False
        self.staff.save()
        c = Client()
        c.force_login(self.staff)
        self.assertEqual(c.get("/ops/feedback").status_code, 404)

    def test_post_not_allowed_for_staff(self):
        self.assertEqual(self.as_(user=self.staff).post("/ops/feedback").status_code, 405)

    def test_pages(self):
        Feedback.objects.bulk_create([Feedback(space=self.hub, member=self.t41, text=f"의견{i:03d}") for i in range(PAGE + 1)])
        c = self.as_(user=self.staff)
        first, second = c.get("/ops/feedback").content.decode(), c.get("/ops/feedback", {"p": 2}).content.decode()
        self.assertEqual(first.count("<tr><td"), PAGE)
        self.assertEqual(second.count("<tr><td"), 1)
        self.assertIn("1 / 2", first)


class CommandQATests(Base):
    def test_bad_since_refused(self):
        for bad in ("7", "7w", "-1d", "abc"):
            with self.assertRaises(CommandError, msg=bad):
                call_command("feedback", "--since", bad)
