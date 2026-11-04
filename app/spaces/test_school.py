"""H2 학교 입장과 공통 틀: 구글 필수 입장, 새 동학년 막기, 학교 메뉴, 게이트, 학교 기준 문구."""
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from collect.models import Notification

from . import views
from .gates import manager_required, school_required, school_write_required
from .merge import open_school
from .models import Member, School, Space
from .test_merge import UNTIL, MergeBase

User = get_user_model()
TOKEN = {"access_token": "t"}


def google(sub, name="새선생"):
    return User.objects.create(username=f"google:{sub}", first_name=name)


class JoinBase(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)
        self.link = f"/j/{self.hub.invite_token}"

    def member_client(self, user):
        c = Client()
        c.force_login(user)
        c.get(self.link)
        return c


class EntryTests(JoinBase):
    def test_anonymous_sees_google_only_and_gets_no_seat(self):
        c = Client()
        n = Member.objects.in_space(self.hub).count()
        page = c.get(self.link, follow=True)
        self.assertContains(page, "/auth/google/login")
        self.assertContains(page, "/join")  # 돌아올 곳
        self.assertNotContains(page, 'name="group"')
        self.assertNotContains(page, 'action="/join"')
        r = c.post("/join", {"group": "office", "role": "교무", "name": "익명"})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("_auth_user_id", c.session)
        self.assertEqual(Member.objects.in_space(self.hub).count(), n)

    @override_settings(GOOGLE_CLIENT_ID="cid")
    def test_google_login_comes_back_to_the_form_and_enters(self):
        c = Client()
        c.get(self.link)
        r = c.get("/auth/google/login", {"next": "/join", "remember": "0"})
        self.assertEqual(r.status_code, 302)
        state = c.session["google_state"]
        info = {"sub": "newcomer", "email": "a@b.kr", "email_verified": True, "name": "신입교사"}
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, info]):
            r = c.get("/auth/google/callback", {"state": state, "code": "x"})
        self.assertRedirects(r, "/join", fetch_redirect_response=False)
        page = c.get("/join")
        self.assertContains(page, "신입교사")  # 구글 이름을 미리 채운다
        self.assertContains(page, "교무실")
        r = c.post("/join", {"group": "office", "role": "교무", "name": "신입교사"})
        self.assertRedirects(r, f"/s/{self.hub.token}/welcome")
        seat = Member.objects.in_space(self.hub).get(user__username="google:newcomer")
        self.assertEqual((seat.group, seat.position, seat.is_manager), ("office", "교무", False))

    def test_every_group_enters_without_approval(self):
        cases = [
            ("office", {"role": "교무부장"}, "교무실", "교무부장", "교무실 · 교무부장"),
            ("admin", {"role": "실장"}, "행정실", "실장", "행정실 · 실장"),
            ("meal", {"role": "영양교사"}, "급식소", "영양교사", "급식소 · 영양교사"),
            ("support", {"role": "보건"}, "기타/지원", "보건", "기타/지원 · 보건"),
            ("g4", {"cls": "4-3"}, "4-3", "3반", "4학년 3반"),
            ("g2", {"cls": "2-전담"}, "2-전담", "전담", "2학년 전담"),
        ]
        for i, (group, extra, cls, position, text) in enumerate(cases):
            with self.subTest(group):
                c = self.member_client(google(f"g{i}"))
                r = c.post("/join", {"group": group, "name": f"선생{i}", **extra})
                self.assertRedirects(r, f"/s/{self.hub.token}/welcome", fetch_redirect_response=False)
                seat = Member.objects.in_space(self.hub).get(name=f"선생{i}")
                self.assertEqual((seat.group, seat.cls, seat.position, seat.cls_text), (group, cls, position, text))
                self.assertEqual(c.get(r["Location"]).status_code, 200)

    def test_custom_role(self):
        c = self.member_client(google("c1"))
        c.post("/join", {"group": "support", "role": "", "role_custom": "늘봄실무사", "name": "직접"})
        self.assertEqual(Member.objects.in_space(self.hub).get(name="직접").position, "늘봄실무사")

    @override_settings(GOOGLE_CLIENT_ID="cid")
    def test_merged_guest_linking_account_of_other_space_gets_space_wording(self):
        guest = User.objects.create(username="guest:link", first_name="옮긴손님")
        Member.objects.create(space=self.hub, user=guest, name="옮긴손님", cls="5-3", group="g5")
        c = Client()
        c.force_login(guest)
        c.get("/auth/google/login")
        info = {"sub": "far", "email": "", "name": "다른학교"}
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, info]):
            r = c.get("/auth/google/callback", {"state": c.session["google_state"], "code": "x"})
        self.assertContains(r, "이미 다른 공간에 들어가 있어요", status_code=409)
        self.assertTrue(Member.objects.filter(user=guest, space=self.hub).exists())

    def test_bad_choices_get_no_seat(self):
        bad = [
            {"group": "", "role": "교무", "name": "틀림"},
            {"group": "hack", "role": "교무", "name": "틀림"},
            {"group": "office", "role": "없는직책", "name": "틀림"},
            {"group": "office", "role": "", "name": "틀림"},
            {"group": "g4", "cls": "5-1", "name": "틀림"},
            {"group": "g4", "cls": "", "name": "틀림"},
            {"group": "office", "role": "교무", "name": "   "},
        ]
        c = self.member_client(google("b1", ""))
        for data in bad:
            with self.subTest(data):
                self.assertEqual(c.post("/join", data).status_code, 400)
        self.assertFalse(Member.objects.filter(user__username="google:b1").exists())

    def test_same_name_two_people_each_get_a_seat(self):
        for sub in ("same1", "same2"):
            r = self.member_client(google(sub)).post("/join", {"group": "g4", "cls": "4-3", "name": "김민수"})
            self.assertEqual(r.status_code, 302)
        seats = Member.objects.in_space(self.hub).filter(name="김민수")
        self.assertEqual({s.user.username for s in seats}, {"google:same1", "google:same2"})

    def test_guest_seat_with_same_name_is_not_taken_nor_doubled(self):
        guest = User.objects.create(username="guest:park", first_name="박손님")
        seat = Member.objects.create(space=self.hub, user=guest, name="박손님", cls="4-3", group="g4")
        c = self.member_client(google("park"))
        r = c.post("/join", {"group": "g4", "cls": "4-3", "name": "박손님"})
        self.assertContains(r, "구글 계정으로 이어 두기", status_code=400)
        seat.refresh_from_db()
        self.assertEqual(seat.user, guest)
        self.assertEqual(Member.objects.in_space(self.hub).filter(name="박손님").count(), 1)

    def test_guest_is_told_to_connect_google_and_gets_no_seat(self):
        guest = User.objects.create(username="guest:free", first_name="떠돌이")
        c = self.member_client(guest)
        page = c.get("/join")
        self.assertContains(page, "구글")
        self.assertContains(page, "/auth/google/login")
        self.assertNotContains(page, 'name="group"')
        r = c.post("/join", {"group": "office", "role": "교무", "name": "떠돌이"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Member.objects.filter(user=guest).exists())

    def test_guest_member_of_other_space_is_blocked(self):
        guest = User.objects.create(username="guest:far", first_name="먼손님")
        Member.objects.create(space=self.far, user=guest, name="먼손님", cls="4-2")
        c = self.member_client(guest)
        r = c.post("/join", {"group": "office", "role": "교무", "name": "먼손님"})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(Member.objects.in_space(self.hub).filter(user=guest).exists())

    def test_member_of_other_school_is_blocked_with_space_wording(self):
        c = self.member_client(self.far_user)
        page = c.get("/join")
        self.assertContains(page, "한 선생님이 공간 하나만")
        r = c.post("/join", {"group": "office", "role": "교무", "name": "끼어들기"})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(Member.objects.in_space(self.hub).filter(user=self.far_user).exists())

    def test_merged_grade_member_just_goes_to_school_space(self):
        """동학년 사람은 합칠 때 옮겨졌으므로 학교 링크를 열어도 새 자리가 생기지 않는다."""
        c = self.member_client(self.u5)
        r = c.get("/join")
        self.assertRedirects(r, f"/s/{self.hub.token}/", fetch_redirect_response=False)
        c.get(self.link)
        r = c.post("/join", {"group": "office", "role": "교무", "name": "겹침"})
        self.assertEqual(Member.objects.filter(user=self.u5).count(), 1)
        self.assertEqual(Member.objects.get(user=self.u5).space, self.hub)
        self.assertEqual(r.status_code, 302)

    def test_double_submit_keeps_one_seat(self):
        c = self.member_client(google("dbl"))
        data = {"group": "office", "role": "교무", "name": "두번"}
        self.assertEqual(c.post("/join", data).status_code, 302)
        c.get(self.link)
        self.assertEqual(c.post("/join", data).status_code, 302)
        self.assertEqual(Member.objects.filter(user__username="google:dbl").count(), 1)

    def test_seat_limit_fits_big_school_and_is_enforced(self):
        self.assertGreaterEqual(views.SCHOOL_SEATS, 200)
        have = Member.objects.in_space(self.hub).count()
        Member.objects.bulk_create(Member(space=self.hub, name=f"선생{i}", cls="교무실", group="office")
                                   for i in range(views.SCHOOL_SEATS - have - 1))
        ok = self.member_client(google("last")).post("/join", {"group": "office", "role": "교무", "name": "막차"})
        self.assertEqual(ok.status_code, 302)
        full = self.member_client(google("over")).post("/join", {"group": "office", "role": "교무", "name": "넘침"})
        self.assertContains(full, "더 들어올 수 없어요", status_code=400)
        self.assertNotContains(full, "동학년", status_code=400)

    def test_htmx_group_pick_redraws_form_keeping_name(self):
        c = self.member_client(google("hx"))
        hx = {"HX-Request": "true"}
        r = c.get("/join", {"group": "g4", "name": "쓰던이름"}, headers=hx)
        self.assertTemplateUsed(r, "spaces/_join_school_form.html")
        self.assertTemplateNotUsed(r, "bare.html")
        self.assertContains(r, 'data-v="4-1"')
        self.assertContains(r, 'data-v="4-전담"')
        self.assertContains(r, "쓰던이름")
        r = c.get("/join", {"group": "meal"}, headers=hx)
        self.assertContains(r, 'data-v="영양교사"')
        self.assertNotContains(r, 'data-v="4-1"')

    def test_only_manager_reissues_and_old_link_closes(self):
        other = Client()
        other.force_login(self.u5)
        self.assertEqual(other.post(f"/s/{self.hub.token}/invite").status_code, 403)
        manager = Client()
        manager.force_login(self.u4)
        self.assertEqual(manager.post(f"/s/{self.hub.token}/invite").status_code, 302)
        self.assertEqual(Client().get(self.link).status_code, 404)

    def test_join_screens_have_school_wording(self):
        anon = Client().get(self.link, follow=True)
        form = self.member_client(google("w1")).get("/join")
        school_mate = self.member_client(self.u4)
        guest = self.member_client(User.objects.create(username="guest:w", first_name="손"))
        for page in (anon, form, school_mate.get(self.link, follow=True), guest.get("/join"),
                     Client().get("/j/nope")):
            self.assertNotContains(page, "동학년", status_code=page.status_code)
            self.assertNotContains(page, "0학년", status_code=page.status_code)
            self.assertNotContains(page, "티로드", status_code=page.status_code)

    def test_invite_message_says_google(self):
        c = Client()
        c.force_login(self.u4)
        page = c.get(f"/s/{self.hub.token}/school")
        self.assertContains(page, "구글")
        self.assertContains(page, self.hub.invite_token)
        self.assertNotContains(page, "가입 없음")


class NewGradeBlockedTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        seat = Member.objects.create(space=Space.objects.create(school=self.school, grade=4, name="4학년"),
                                     user=google("ncboss"), name="관리", cls="4-1")
        open_school(self.school, UNTIL, seat)
        self.free = School.objects.create(neis_atpt="J10", neis_code="7000000", name="옆초등학교")
        self.client.force_login(google("nc"))
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2"])
        p.start()
        self.addCleanup(p.stop)

    def pick(self, school):
        s = self.client.session
        s[views.PICKED] = {"atpt": school.neis_atpt, "code": school.neis_code, "name": school.name, "office": "경기"}
        s.save()

    def test_plan_school_shows_notice_not_form(self):
        self.pick(self.school)
        page = self.client.get("/new/class")
        self.assertContains(page, "이 학교는 학교 공간을 씁니다. 담당 선생님께 학교 링크를 받아 주세요")
        self.assertNotContains(page, "동학년 만들기</button>")

    def test_post_creates_nothing(self):
        self.pick(self.school)
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "만들기"})
        self.assertContains(r, "이 학교는 학교 공간을 씁니다", status_code=409)
        self.assertEqual(Space.objects.filter(school=self.school).count(), 2)  # 학교 공간과 합쳐진 옛 공간뿐
        self.assertFalse(Member.objects.filter(name="만들기").exists())

    def test_free_school_still_creates_grade(self):
        self.pick(self.free)
        r = self.client.post("/new/class", {"grade": 4, "cls": "4-1", "name": "만들기"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Space.objects.filter(school=self.free, grade=4).exists())


class MenuTests(JoinBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.u5)
        self.base = f"/s/{self.hub.token}"

    def test_school_space_has_board_and_school_with_real_content(self):
        page = self.client.get(f"{self.base}/today")
        self.assertContains(page, f'href="{self.base}/board"')
        self.assertContains(page, f'href="{self.base}/school"')
        school = self.client.get(f"{self.base}/school")
        self.assertContains(school, "교직원 <b class=\"num\">3</b>명")
        self.assertContains(school, "4학년")
        self.assertContains(school, "교무실")
        self.assertNotContains(school, "준비 중")
        board = self.client.get(f"{self.base}/board")
        self.assertContains(board, "아직 올라온 공지가 없어요")
        self.assertNotContains(board, "준비 중")

    def test_grade_space_has_no_school_menu_and_404(self):
        c = Client()
        c.force_login(self.far_user)
        base = f"/s/{self.far.token}"
        page = c.get(f"{base}/today")
        self.assertNotContains(page, f'href="{base}/board"')
        self.assertNotContains(page, f'href="{base}/school"')
        self.assertEqual(c.get(f"{base}/school").status_code, 404)
        self.assertEqual(c.get(f"{base}/board").status_code, 404)

    def test_other_school_member_404_anonymous_to_login(self):
        c = Client()
        c.force_login(self.far_user)
        for path in ("/school", "/board"):
            self.assertEqual(c.get(self.base + path).status_code, 404)
            self.assertEqual(Client().get(self.base + path).status_code, 302)

    def test_top_and_side_are_school_based(self):
        page = self.client.get(f"{self.base}/today")
        self.assertContains(page, "우리 학교")
        self.assertContains(page, "학교 공간 · 3명")
        self.assertContains(page, 'aria-label="학교"')


class ExpiryTests(JoinBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.u5)
        self.base = f"/s/{self.hub.token}"

    def expire(self, **kw):
        School.objects.filter(pk=self.school.pk).update(**kw)

    def test_plan_writable_rules(self):
        today = timezone.localdate()
        for kw, want in (({"plan": "school", "paid_until": today}, True),
                         ({"plan": "school", "paid_until": today - timedelta(days=1)}, False),
                         ({"plan": "free", "paid_until": today + timedelta(days=30)}, False)):
            self.expire(**kw)
            self.assertEqual(School.objects.get(pk=self.school.pk).writable, want, kw)

    def test_expired_reads_with_one_line_notice_and_collect_lives(self):
        notice = "학교 플랜 기간이 끝났어요"
        self.assertNotContains(self.client.get(f"{self.base}/today"), notice)
        self.expire(paid_until=date(2020, 1, 1))
        for path in ("/today", "/school", "/board", "/c/"):
            self.assertContains(self.client.get(self.base + path), notice)

    def test_grade_space_never_shows_notice(self):
        c = Client()
        c.force_login(self.far_user)
        self.assertNotContains(c.get(f"/s/{self.far.token}/today"), "학교 플랜 기간")


def ok(request):
    return HttpResponse("ok")


class GateTests(JoinBase):
    def call(self, view, space, member, method="get"):
        request = getattr(RequestFactory(), method)("/x")
        request.space, request.member = space, member
        return view(request)

    def test_school_required(self):
        view = school_required(ok)
        self.assertEqual(self.call(view, self.hub, self.m5).status_code, 200)
        with self.assertRaises(Http404):
            self.call(view, self.far, self.far_member)

    def test_manager_required(self):
        view = manager_required(ok)
        self.m4.refresh_from_db()
        self.assertEqual(self.call(view, self.hub, self.m4).status_code, 200)
        self.assertEqual(self.call(view, self.hub, self.m5).status_code, 403)
        self.assertEqual(self.call(view, self.far, self.far_member).status_code, 403)

    def test_school_write_required(self):
        view = school_write_required(ok)
        self.assertEqual(self.call(view, self.hub, self.m5, "post").status_code, 200)
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        hub = Space.objects.get(pk=self.hub.pk)
        self.assertEqual(self.call(view, hub, self.m5, "post").status_code, 403)
        self.assertEqual(self.call(view, hub, self.m5, "get").status_code, 200)
        School.objects.filter(pk=self.school.pk).update(paid_until=UNTIL, plan="free")
        hub = Space.objects.get(pk=self.hub.pk)
        self.assertEqual(self.call(view, hub, self.m5, "post").status_code, 403)


class WordingTests(JoinBase):
    """학교 공간 화면 전체에 '동학년'·'0학년'이 없다."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.u5)
        self.base = f"/s/{self.hub.token}"
        self.guest = User.objects.create(username="guest:merged", first_name="손")
        Member.objects.create(space=self.hub, user=self.guest, name="손", cls="5-4", group="g5")

    def clean(self, r, label):
        self.assertEqual(r.status_code, 200, label)
        body = r.content.decode()
        self.assertNotIn("동학년", body, label)
        self.assertNotIn("0학년", body, label)
        self.assertNotIn("티로드", body, label)

    def test_every_school_screen(self):
        hx = {"HX-Request": "true"}
        c = self.c5.key
        for path in ("/members", "/welcome", "/today", "/schedule", "/school", "/school?tab=duty", "/school/me", "/board", "/bell",
                     "/c/", "/c/new", "/c/new?src=paste", "/c/new?src=hwp", "/c/new?src=preset", f"/c/{c}/", f"/c/{c}/live",
                     "/settings", "/settings?a=alerts", "/settings?a=space", "/settings?a=device"):
            self.clean(self.client.get(self.base + path), path)
        self.clean(self.client.get(self.base + "/members", headers=hx), "members hx")
        self.clean(self.client.get("/forms"), "forms")

    def test_guest_screens(self):
        c = Client()
        c.force_login(self.guest)
        for path in ("/welcome", "/settings?a=space"):
            self.clean(c.get(self.base + path), path)

    def test_paste_preview_and_messages(self):
        r = self.client.post(f"{self.base}/c/new/preview", {"src": "paste", "paste": "이름\t연락처\n가\t나\n"},
                             headers={"HX-Request": "true"})
        self.clean(r, "preview")
        r = self.client.post(f"{self.base}/settings/me", {"name": "  "}, headers={"HX-Request": "true"})
        self.assertNotContains(r, "동학년", status_code=400)
        r = self.client.post(f"{self.base}/settings/leave", {"confirm": "아님"})
        self.assertNotContains(r, "동학년", status_code=400)

    def test_today_edit_toast(self):
        from today.models import Event
        e = Event.objects.create(space=self.hub, by=self.m5, date=timezone.localdate(), slot="b1", slot_end="b1", title="회의")
        new_day = (timezone.localdate() + timedelta(days=1)).isoformat()
        r = self.client.post(f"{self.base}/today/e/{e.key}", {"date": new_day, "slot": "b1", "place": ""},
                             headers={"HX-Request": "true"})
        self.assertContains(r, "학교 「바뀐 것」")
        self.assertNotContains(r, "동학년")

    def test_quota_message(self):
        from hwpfiles.models import ResultFile
        from hwpfiles.views import reject_text
        self.assertTrue(reject_text("Quota", self.hub).startswith("학교 저장 공간"))
        self.assertTrue(reject_text("Quota", self.far).startswith("동학년 저장 공간"))
        self.assertTrue(ResultFile(space=self.hub, error="Quota").error_text().startswith("학교 저장 공간"))
        self.assertTrue(ResultFile(space=self.far, error="Quota").error_text().startswith("동학년 저장 공간"))

    def test_grade_space_keeps_its_words(self):
        c = Client()
        c.force_login(self.far_user)
        base = f"/s/{self.far.token}"
        self.assertContains(c.get(f"{base}/today"), "동학년 바뀐 것")
        self.assertContains(c.get(f"{base}/schedule"), "동학년 일정에 올리기")
        self.assertContains(c.get(f"{base}/settings?a=space"), "동학년에서 나가기")
        self.assertContains(c.get(f"{base}/c/{self.far_c.key}/"), "동학년 화면으로")
        self.assertContains(c.get(f"{base}/c/new"), "동학년 1반")


class SettingsTests(JoinBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.u5)
        self.base = f"/s/{self.hub.token}"

    def test_specialist_is_not_shown_as_homeroom(self):
        Member.objects.create(space=self.hub, user=google("s1"), name="전담샘", cls="4-전담", group="g4", position="전담")
        Member.objects.create(space=self.hub, user=google("s2"), name="교무샘", cls="교무실", group="office", position="교무")
        html = self.client.get(f"{self.base}/settings?a=space").content.decode()
        row = html[html.index("4학년 전담 전담샘"):]
        self.assertIn("전담</span>", row[:row.index("</div></div>") + 80])
        self.assertLess(html.index("전담샘"), html.index("교무샘"))
        row = html[html.index("5학년 1반 김도윤"):]
        self.assertIn("담임</span>", row[:row.index("</div></div>") + 80])

    def test_grade_space_specialist_too(self):
        c = Client()
        u = google("s3")
        Member.objects.create(space=self.far, user=u, name="먼전담", cls="4-전담")
        c.force_login(self.far_user)
        html = c.get(f"/s/{self.far.token}/settings?a=space").content.decode()
        row = html[html.index("전담 먼전담"):]
        self.assertIn(">전담</span>", row[:row.index("</div></div>") + 80])

    def test_office_member_can_rename_without_class_picks(self):
        u = google("o1")
        m = Member.objects.create(space=self.hub, user=u, name="교무샘", cls="교무실", group="office", position="교무")
        c = Client()
        c.force_login(u)
        page = c.get(f"{self.base}/settings")
        self.assertContains(page, "교무실 · 교무")
        self.assertNotContains(page, 'name="cls" value="4-1"')
        r = c.post(f"{self.base}/settings/me", {"name": "새이름"})
        self.assertEqual(r.status_code, 302)
        m.refresh_from_db()
        self.assertEqual((m.name, m.cls, m.group), ("새이름", "교무실", "office"))

    def test_teacher_moves_class_position_follows_and_every_grade_is_offered(self):
        self.assertContains(self.client.get(f"{self.base}/settings"), 'value="2-1"')
        self.client.post(f"{self.base}/settings/me", {"cls": "2-전담"})
        self.m5.refresh_from_db()
        self.assertEqual((self.m5.cls, self.m5.group, self.m5.position), ("2-전담", "g2", "전담"))

    def test_teacher_cannot_pick_non_grade_value_of_others(self):
        Member.objects.create(space=self.hub, user=google("o2"), name="교무샘", cls="교무실", group="office")
        r = self.client.post(f"{self.base}/settings/me", {"cls": "교무실"})
        self.assertEqual(r.status_code, 302)
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.cls, "5-1")

    def test_same_name_is_allowed_in_school(self):
        Member.objects.create(space=self.hub, user=google("o3"), name="김도윤2", cls="5-1", group="g5")
        self.client.post(f"{self.base}/settings/me", {"name": "김도윤2"})
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.name, "김도윤2")


class RosterFitTests(JoinBase):
    def test_collect_people_rows_show_office_and_class_rows_skip_them(self):
        """학년 밖 교직원은 담임 줄에 끼지 않는다(다음 작업 H4 전에도 줄이 깨지지 않게)."""
        from collect.recipients import roster_rows
        Member.objects.create(space=self.hub, user=google("r1"), name="교무샘", cls="교무실", group="office", position="교무")
        self.assertNotIn("교무샘", [r["who"] for r in roster_rows(self.hub, "class")])
        people = {r["who"]: r["unit"] for r in roster_rows(self.hub, "people")}
        self.assertEqual(people["교무샘"], "교무실 · 교무")


class ExpiredEntryTests(JoinBase):
    """리드 결정: 만료 때 막는 것은 새 교직원 입장과 학교판 전용 쓰기뿐. 실제 주소로 본다."""

    def setUp(self):
        super().setUp()
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        self.base = f"/s/{self.hub.token}"
        self.manager = Client()
        self.manager.force_login(self.u4)
        self.teacher = Client()
        self.teacher.force_login(self.u5)

    def test_new_google_member_gets_notice_and_no_seat(self):
        c = self.member_client(google("late"))
        self.assertContains(c.get("/join"), "학교 플랜이 끝나 새로 들어올 수 없어요")
        r = c.post("/join", {"group": "office", "role": "교무", "name": "늦은이"})
        self.assertContains(r, "학교 플랜이 끝나 새로 들어올 수 없어요", status_code=403)
        self.assertFalse(Member.objects.filter(name="늦은이").exists())

    def test_anonymous_gets_notice_not_google_button(self):
        page = Client().get(self.link, follow=True)
        self.assertContains(page, "학교 플랜이 끝나 새로 들어올 수 없어요")
        self.assertNotContains(page, "/auth/google/login")

    def test_plan_off_blocks_entry_too(self):
        School.objects.filter(pk=self.school.pk).update(plan="free", paid_until=UNTIL)
        c = self.member_client(google("off"))
        self.assertEqual(c.post("/join", {"group": "office", "role": "교무", "name": "꺼짐"}).status_code, 403)

    def test_existing_member_still_goes_in_through_link(self):
        c = self.member_client(self.u5)
        self.assertRedirects(c.get("/join"), f"{self.base}/", fetch_redirect_response=False)

    def test_existing_members_keep_writing(self):
        r = self.teacher.post(f"{self.base}/today/events", {"line": "내일 2블록 과학실 안전교육"})
        self.assertEqual(r.status_code, 303)
        self.assertTrue(self.hub.events.filter(title__contains="안전교육").exists())
        self.assertEqual(self.teacher.post(f"{self.base}/settings/me", {"name": "바꾼이름"}).status_code, 302)
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.name, "바꾼이름")
        r = self.teacher.post(f"{self.base}/c/new", {"src": "paste", "title": "만료 뒤 모으기", "paste": "이름\t연락처\n가\t나\n"})
        self.assertLess(r.status_code, 400)
        self.assertTrue(self.hub.collections.filter(title="만료 뒤 모으기").exists())

    def test_manager_still_reissues_invite(self):
        old = self.hub.invite_token
        self.assertEqual(self.manager.post(f"{self.base}/invite").status_code, 302)
        self.hub.refresh_from_db()
        self.assertNotEqual(self.hub.invite_token, old)

    def test_notice_says_what_is_true(self):
        page = self.teacher.get(f"{self.base}/today")
        self.assertContains(page, "새 선생님은 들어올 수 없고")
        self.assertNotContains(page, "공지")
        self.assertNotContains(page, "복무")


class GradeSpaceGateTests(JoinBase):
    def test_write_gate_passes_grade_space_even_on_free_plan(self):
        request = RequestFactory().post("/x")
        request.space, request.member = self.far, self.far_member
        self.assertFalse(self.far.school.writable)
        self.assertEqual(school_write_required(ok)(request).status_code, 200)


class JoinRaceTests(JoinBase):
    """입력을 읽은 뒤 잠금을 잡기 전에 공간이 바뀌는 경우. _clean_name이 불리는 순간에 끼워 넣는다."""

    def between(self, action):
        real = views._clean_name
        done = []

        def hook(raw):
            if not done:
                done.append(1)
                action()
            return real(raw)

        return mock.patch("spaces.views._clean_name", side_effect=hook)

    def test_grade_invite_merged_while_joining_gets_no_seat(self):
        late = Space.objects.create(school=self.school, grade=2, name="새솔초 2학년")
        c = Client()
        c.get(f"/j/{late.invite_token}")
        with self.between(lambda: open_school(self.school, UNTIL)):
            r = c.post("/join", {"cls": "2-1", "name": "늦은손님"})
        self.assertContains(r, "학교 공간으로 바뀌었어요")
        self.assertFalse(Member.objects.filter(name="늦은손님").exists())
        self.assertNotIn("_auth_user_id", c.session)
        self.assertNotIn(views.INVITE, c.session)

    def test_grade_invite_reissued_while_joining_gets_no_seat(self):
        c = Client()
        c.get(f"/j/{self.far.invite_token}")

        def reissue():
            Space.objects.filter(pk=self.far.pk).update(invite_token="new-token")

        with self.between(reissue):
            r = c.post("/join", {"cls": "4-2", "name": "늦은손님"})
        self.assertContains(r, "링크가 새로 바뀌었어요")
        self.assertFalse(Member.objects.filter(name="늦은손님").exists())

    def test_school_invite_reissued_while_joining_gets_no_seat(self):
        c = self.member_client(google("race"))

        def reissue():
            Space.objects.filter(pk=self.hub.pk).update(invite_token="new-token")

        with self.between(reissue):
            r = c.post("/join", {"group": "office", "role": "교무", "name": "늦은이"})
        self.assertContains(r, "링크가 새로 바뀌었어요")
        self.assertNotContains(r, "동학년")
        self.assertFalse(Member.objects.filter(name="늦은이").exists())


class InviteVisibilityTests(JoinBase):
    def setUp(self):
        super().setUp()
        self.base = f"/s/{self.hub.token}"
        self.teacher = Client()
        self.teacher.force_login(self.u5)
        self.manager = Client()
        self.manager.force_login(self.u4)

    def test_teacher_never_gets_school_link_on_any_screen(self):
        for path in ("/", "/members", "/welcome", "/today", "/school", "/settings", "/settings?a=space", "/made"):
            r = self.teacher.get(self.base + path, follow=True)
            self.assertEqual(r.status_code, 200, path)
            body = r.content.decode()
            self.assertNotIn(self.hub.invite_token, body, path)
            self.assertNotIn('id="msg"', body, path)
            self.assertNotIn('data-copy="#msg"', body, path)
            self.assertNotIn("data:image/svg", body, path)
        self.assertEqual(self.teacher.post(f"{self.base}/invite").status_code, 403)

    def test_manager_gets_link_qr_and_note(self):
        for path in ("/school", "/settings?a=space"):
            r = self.manager.get(self.base + path)
            self.assertContains(r, self.hub.invite_token)
            self.assertContains(r, 'id="msg"')
            self.assertContains(r, "data:image/svg")

    def test_grade_space_members_all_still_see_link(self):
        Member.objects.create(space=self.far, user=google("f2"), name="둘째", cls="4-2")
        c = Client()
        c.force_login(User.objects.get(username="google:f2"))
        self.assertContains(c.get(f"/s/{self.far.token}/"), self.far.invite_token)
        r = c.get(f"/s/{self.far.token}/members", headers={"HX-Request": "true"})
        self.assertContains(r, "쪽지 문구 다시 복사")


class WelcomeAlertTests(JoinBase):
    def enter(self, sub="n1", **data):
        c = self.member_client(google(sub))
        return c.post("/join", {"group": "office", "role": "교무부장", "name": "새얼굴", **data})

    def test_all_managers_get_bell_only(self):
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        self.assertEqual(self.enter().status_code, 302)
        alerts = Notification.objects.in_space(self.hub).filter(text__contains="새얼굴")
        self.assertEqual({a.member_id for a in alerts}, {self.m4.pk, self.m5.pk})
        self.assertEqual({a.text for a in alerts}, {"새얼굴 선생님(교무실 · 교무부장)이 들어왔어요"})
        self.assertTrue(all(a.pushed for a in alerts))  # 웹 푸시 묶음에는 들어가지 않는다
        c = Client()
        c.force_login(self.u4)
        html = c.get(f"/s/{self.hub.token}/bell").content.decode()
        line = html[html.index("새얼굴 선생님"):]
        self.assertIn("새 선생님 ·", line[:line.index("</form>")])

    def test_teacher_gets_none_and_refused_entry_makes_none(self):
        self.enter()
        self.assertFalse(Notification.objects.filter(member=self.m4b, text__contains="새얼굴").exists())
        n = Notification.objects.count()
        self.enter("n2", name="")
        self.assertEqual(Notification.objects.count(), n)

    def test_grade_join_has_no_such_alert(self):
        c = Client()
        c.get(f"/j/{self.far.invite_token}")
        self.assertEqual(c.post("/join", {"cls": "4-2", "name": "먼손님"}).status_code, 302)
        self.assertFalse(Notification.objects.filter(text__contains="먼손님").exists())
