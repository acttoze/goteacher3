"""V6 의견 보내기.

불변식
- 계정 회원(구글·아이디)만 보낸다. 손님·비로그인·다른 공간 회원은 못 보낸다(손님 403, 남의 공간 404).
- 저장은 누가(회원 자리)·어느 공간·종류·글·경로 모양·화면 크기·시각. 경로는 주소 규칙 모양만(공간 토큰·키·쿼리 없음).
- 화면 내용(이름·일정 원문)은 보내는 칸이 없다. 보낸 글만 저장한다.
- 사람당 짧은 시간에 여러 번 보내면 429(저장 안 함). 돌아가기는 막혀도 구 사이트로 간다(저장만 안 함).
- 의견함·관리 명령은 운영자(is_staff)만. 그 외 404.
- 윗띠는 이관 학교 + LEGACY_READONLY에서 계정 회원에게만.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from legacy.models import LegacyMap
from spaces.models import Member, School, Space

from .models import Feedback
from .views import PER_WINDOW, path_shape

User = get_user_model()
HX = {"HX-Request": "true"}
SITE = "https://old.example.test"
ON = override_settings(LEGACY_READONLY=True, LEGACY_SITE_URL=SITE)
THANKS = "고마워요, 꼭 읽을게요"


class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        for p in (mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531000", name="새솔초", plan="school", paid_until=timezone.now().date() + timedelta(days=90))
        self.hub = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        self.other = Space.objects.create(school=School.objects.create(neis_atpt="C10", neis_code="2", name="옆초"), grade=4, name="옆학교 4학년")
        self.base = f"/s/{self.hub.token}"
        self.t41 = self.seat("4-1", "사일", "g4")
        self.boss = self.seat("교무실", "부장", "office")
        g = User.objects.create(username="guest:abc", first_name="손님")
        self.guest = Member.objects.create(space=self.hub, user=g, name="손님", cls="4-3", group="g4")
        self.far = self.seat("4-1", "먼곳", "", space=self.other)

    def seat(self, cls, name, group, space=None):
        u = User.objects.create(username=f"google:{name}", first_name=name)
        return Member.objects.create(space=space or self.hub, user=u, name=name, cls=cls, group=group)

    def as_(self, m=None, user=None):
        c = Client()
        c.force_login(user or m.user)
        return c

    def send(self, m, **data):
        body = {"text": "달력 글씨가 작아요", "kind": "bad", "path": f"{self.base}/today?w=2026-11-16", "screen": "1536x750", **data}
        return self.as_(m).post(f"{self.base}/feedback", body, headers=HX)


class SendTests(Base):
    def test_saves_who_where_kind_text_path_screen(self):
        r = self.send(self.t41)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, THANKS)
        f = Feedback.objects.get()
        self.assertEqual((f.space, f.member, f.kind, f.text, f.path, f.screen), (self.hub, self.t41, "bad", "달력 글씨가 작아요", "/s/<공간>/today", "1536x750"))
        self.assertIsNotNone(f.created_at)

    def test_kind_optional_multiline_kept(self):
        self.assertEqual(self.send(self.t41, kind="", text="첫 줄\r\n둘째 줄").status_code, 200)
        f = Feedback.objects.get()
        self.assertEqual((f.kind, f.text), ("", "첫 줄\n둘째 줄"))

    def test_empty_long_and_bad_kind_refused(self):
        for data in ({"text": "  "}, {"text": "가" * 1001}, {"kind": "back_slow"}, {"kind": "zzz"}):
            self.assertEqual(self.send(self.t41, **data).status_code, 400, data)
        self.assertFalse(Feedback.objects.exists())
        self.assertEqual(self.send(self.t41, text="가" * 1000).status_code, 200)

    def test_bad_screen_dropped(self):
        self.send(self.t41, screen="<script>")
        self.assertEqual(Feedback.objects.get().screen, "")

    def test_guest_refused(self):
        self.assertEqual(self.send(self.guest).status_code, 403)
        self.assertEqual(self.as_(self.guest).post(f"{self.base}/feedback/back", {"kind": "back_slow"}).status_code, 403)
        self.assertFalse(Feedback.objects.exists())

    def test_other_space_404_and_anon_to_login(self):
        self.assertEqual(self.send(self.far).status_code, 404)
        r = Client().post(f"{self.base}/feedback", {"text": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Feedback.objects.exists())

    def test_get_not_allowed(self):
        self.assertEqual(self.as_(self.t41).get(f"{self.base}/feedback").status_code, 405)

    def test_ratelimit_per_person(self):
        for _ in range(PER_WINDOW):
            self.assertEqual(self.send(self.t41).status_code, 200)
        r = self.send(self.t41)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(Feedback.objects.count(), PER_WINDOW)
        self.assertEqual(self.send(self.boss).status_code, 200)  # 다른 사람은 따로 센다

    def test_no_screen_content_fields(self):
        """보내는 칸은 종류·글·경로·화면 크기뿐. 다른 값(이름·일정 제목)을 보내도 저장하지 않는다."""
        self.send(self.t41, name="사일", title="비밀일정")
        f = Feedback.objects.get()
        stored = " ".join(str(getattr(f, x.attname)) for x in Feedback._meta.concrete_fields)
        self.assertNotIn("비밀일정", stored)
        self.assertNotIn(self.hub.token, stored)


class PathShapeTests(Base):
    def test_tokens_keys_query_hidden(self):
        t = self.hub.token
        self.assertEqual(path_shape(f"/s/{t}/today"), "/s/<공간>/today")
        self.assertEqual(path_shape(f"/s/{t}/c/abcdEFGH123456/?x=1#a"), "/s/<공간>/c/<key>/")
        self.assertEqual(path_shape(f"https://t.goteacher.xyz/s/{t}/schedule?w=2026-11-16"), "/s/<공간>/schedule")
        self.assertEqual(path_shape("/c/SECRETLINKTOKEN123"), "/c/<link_token>")
        self.assertEqual(path_shape("/forms"), "/forms")

    def test_unknown_or_junk_empty(self):
        for p in ("/no/such/place", "", "javascript:alert(1)", "/s/" + "a" * 600):
            self.assertEqual(path_shape(p), "", p)

    def test_stored_path_never_has_token(self):
        self.send(self.t41, path=f"/s/{self.hub.token}/board/KEYKEYKEY123?q=비밀")
        p = Feedback.objects.get().path
        self.assertNotIn(self.hub.token, p)
        self.assertNotIn("KEYKEYKEY123", p)
        self.assertNotIn("비밀", p)


class BackTests(Base):
    def setUp(self):
        super().setUp()
        LegacyMap.objects.create(kind="school", legacy_id=self.school.neis_code, obj_id=self.school.pk)

    def back(self, m, **data):
        return self.as_(m).post(f"{self.base}/feedback/back", {"path": f"{self.base}/today", "screen": "1536x750", **data}, headers=HX)

    @ON
    def test_reason_saved_then_legacy_site(self):
        r = self.back(self.t41, kind="back_slow", text="일정이 늦게 떠요")
        self.assertEqual(r.status_code, 204)  # 이동은 화면(feedback.js)이 띠의 data-site로 한다
        f = Feedback.objects.get()
        self.assertEqual((f.kind, f.text, f.path, f.member), ("back_slow", "일정이 늦게 떠요", "/s/<공간>/today", self.t41))

    @ON
    def test_skip_or_no_reason_is_skip(self):
        self.back(self.t41, kind="back_slow", text="무시", skip="1")
        self.back(self.boss, kind="", text="")
        self.assertEqual(sorted(Feedback.objects.values_list("kind", "text")), [("back_skip", ""), ("back_skip", "")])

    @ON
    def test_general_kind_not_taken_as_reason(self):
        self.back(self.t41, kind="bad")
        self.assertEqual(Feedback.objects.get().kind, "back_skip")

    @ON
    def test_ratelimited_still_goes_back(self):
        for _ in range(PER_WINDOW):
            self.send(self.t41)
        r = self.back(self.t41, kind="back_slow")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Feedback.objects.filter(kind="back_slow").exists())

    def test_back_only_while_legacy_on(self):
        self.assertEqual(self.back(self.t41, kind="back_slow").status_code, 404)
        self.assertFalse(Feedback.objects.exists())


class ShellTests(Base):
    def test_button_and_dialog_for_account_member(self):
        r = self.as_(self.t41).get(f"{self.base}/today")
        self.assertContains(r, 'id="fbDlg"')
        self.assertContains(r, 'data-open="#fbDlg"')
        self.assertNotContains(r, 'id="previewBand"')
        for kind in ("불편", "오류", "바라는 점", "좋아요"):
            self.assertContains(r, kind)

    def test_guest_has_no_button(self):
        r = self.as_(self.guest).get(f"{self.base}/today")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'id="fbDlg"')

    def test_band_only_legacy_school_with_setting(self):
        url = f"{self.base}/today"
        with ON:
            self.assertNotContains(self.as_(self.t41).get(url), 'id="previewBand"')  # 이관 학교 아님
        LegacyMap.objects.create(kind="school", legacy_id=self.school.neis_code, obj_id=self.school.pk)
        self.assertNotContains(self.as_(self.t41).get(url), 'id="previewBand"')  # 설정 꺼짐
        with ON:
            r = self.as_(self.t41).get(url)
            self.assertContains(r, 'id="previewBand"')
            self.assertContains(r, "새 고티처 미리 써보기 중")
            self.assertContains(r, 'id="backDlg"')
            self.assertContains(r, '<button class="btn sec sm" type="button" data-open="#backDlg">예전 고티처로</button>')  # 글자가 아닌 단추(11/17)
            for reason in ("찾는 게 없어요", "익숙지 않아요", "느려요", "데이터가 달라요", "그냥 둘러봤어요", "건너뛰기"):
                self.assertContains(r, reason)
            self.assertContains(r, f'data-site="{SITE}"')
            self.assertNotContains(self.as_(self.guest).get(url), 'id="previewBand"')


class InboxTests(Base):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create(username="google:ops", is_staff=True)
        self.send(self.t41, text="첫 의견", kind="bug")
        self.send(self.boss, text="둘째 의견", kind="good")
        Feedback.objects.create(space=self.other, member=self.far, kind="back_slow", text="옆 학교 의견", path="/s/<공간>/today")

    def test_staff_sees_newest_first_with_space_and_group_not_name(self):
        r = self.as_(user=self.staff).get("/ops/feedback")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertLess(body.index("옆 학교 의견"), body.index("둘째 의견"))
        self.assertLess(body.index("둘째 의견"), body.index("첫 의견"))
        self.assertIn("새솔초", body)
        self.assertIn("4학년", body)
        self.assertNotIn("사일", body)
        self.assertNotIn(self.hub.token, body)

    def test_filters(self):
        c = self.as_(user=self.staff)
        r = c.get("/ops/feedback", {"k": "bug"})
        self.assertContains(r, "첫 의견")
        self.assertNotContains(r, "둘째 의견")
        r = c.get("/ops/feedback", {"k": "back"})
        self.assertContains(r, "옆 학교 의견")
        self.assertNotContains(r, "첫 의견")
        r = c.get("/ops/feedback", {"s": "새솔초"})
        self.assertContains(r, "첫 의견")
        self.assertNotContains(r, "옆 학교 의견")

    def test_non_staff_404(self):
        for c in (Client(), self.as_(self.t41), self.as_(self.guest)):
            self.assertEqual(c.get("/ops/feedback").status_code, 404)


class CommandTests(Base):
    def test_since_plain_table(self):
        self.send(self.t41, text="요즘 의견\n둘째 줄\x1b[31m", kind="wish")
        old = Feedback.objects.create(space=self.hub, member=self.boss, text="오래된 의견")
        Feedback.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=8))
        out = StringIO()
        call_command("feedback", "--since", "7d", stdout=out)
        text = out.getvalue()
        self.assertIn("요즘 의견 둘째 줄", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("오래된 의견", text)
        self.assertIn("새솔초", text)
        self.assertIn("바라는 점", text)
        self.assertIn("/s/<공간>/today", text)
        self.assertNotIn("사일", text)


class SecurityFollowupTests(Base):
    """V6 보안 검토 반영: 보관 기간 파기·처리방침 고지·학생 정보 안내·캐시가 죽어도 막는 시도 제한."""

    def test_purge_deletes_after_keep_days(self):
        from hwpfiles.retention import purge
        self.send(self.t41)
        old = Feedback.objects.create(space=self.hub, member=self.boss, text="오래된 의견")
        Feedback.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=366))
        with override_settings(FEEDBACK_KEEP_DAYS=365):
            self.assertEqual(purge(timezone.now(), dry_run=True)["feedback"], 1)
            self.assertEqual(Feedback.objects.count(), 2)
            self.assertEqual(purge(timezone.now())["feedback"], 1)
        self.assertEqual(list(Feedback.objects.values_list("text", flat=True)), ["달력 글씨가 작아요"])
        with override_settings(FEEDBACK_KEEP_DAYS=0):
            purge(timezone.now())
        self.assertFalse(Feedback.objects.exists())

    def test_privacy_mentions_feedback_and_keep(self):
        from .views import keep_text
        r = Client().get("/privacy")
        self.assertContains(r, "의견 보내기")
        self.assertContains(r, f"의견: 받은 날부터 {keep_text()}이 지나면 파기")
        self.assertContains(r, "작성자 연결")
        with override_settings(FEEDBACK_KEEP_DAYS=365):
            self.assertEqual(keep_text(), "1년")
        with override_settings(FEEDBACK_KEEP_DAYS=90):
            self.assertEqual(keep_text(), "90일")

    def test_dialog_warns_student_info(self):
        self.assertContains(self.as_(self.t41).get(f"{self.base}/today"), "학생 이름·연락처는 적지 말아 주세요")

    def test_ratelimit_holds_when_cache_down(self):
        from config import ratelimit
        ratelimit._LOCAL.clear()
        self.addCleanup(ratelimit._LOCAL.clear)
        broken = mock.MagicMock()
        broken.__getitem__.side_effect = ConnectionError
        with mock.patch.object(ratelimit, "caches", broken):
            for _ in range(PER_WINDOW):
                self.assertEqual(self.send(self.t41).status_code, 200)
            self.assertEqual(self.send(self.t41).status_code, 429)
        self.assertEqual(Feedback.objects.count(), PER_WINDOW)
