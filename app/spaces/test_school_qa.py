"""H2 독립 검증(gt-qa): test_school.py에 없는 경계만."""
import re
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import path

from config.urls import urlpatterns as real_urls
from today.models import Event

from . import views
from .gates import manager_required, school_required, school_write_required
from .models import Member, School, Space
from .test_merge import MergeBase
from .test_school import TOKEN, JoinBase, google

User = get_user_model()
BANNED = ("동학년", "0학년", "티로드")
LINK = re.compile(r'(?:href|hx-get|action)="(/[^"#]*)"')
# /school-plan·/privacy·/terms: 학교 밖 공개 문서라 무료판(동학년) 설명이 정상이다
SKIP = re.compile(r"^/(static|auth/|media|school-plan|privacy|terms)|^/forms/.+/download")


def _say(request):
    return HttpResponse("ok")


urlpatterns = [
    path("s/<str:space_token>/qa-school-write", school_required(school_write_required(_say))),
    path("s/<str:space_token>/qa-manager-write", school_required(manager_required(school_write_required(_say)))),
    *real_urls,
]


class CrawlQa(JoinBase):
    """사람이 실제 누를 수 있는 링크를 첫 화면부터 따라가며 금지 낱말을 본다(경로 목록을 사람이 고르지 않게)."""

    def setUp(self):
        super().setUp()
        self.office_user = google("office", "교무샘")
        self.office = Member.objects.create(space=self.hub, user=self.office_user, name="교무샘", cls="교무실",
                                            group="office", position="교무부장")
        self.guest = User.objects.create(username="guest:merged", first_name="손")
        Member.objects.create(space=self.hub, user=self.guest, name="손", cls="5-4", group="g5")
        self.jeon = google("jeon", "전담샘")
        Member.objects.create(space=self.hub, user=self.jeon, name="전담샘", cls="4-전담", group="g4", position="전담")
        Event.objects.create(space=self.hub, by=self.office, date=date.today(), slot="b1", slot_end="b1", title="교직원회의")

    def crawl(self, user):
        c = Client()
        c.force_login(user)
        seen, bad = {}, []
        queue = [f"/s/{self.hub.token}/school"]
        while queue:
            url = queue.pop()
            if url in seen or SKIP.search(url) or url == "/join":
                continue
            r = c.get(url)
            seen[url] = r.status_code
            self.assertLess(r.status_code, 500, url)
            if not r.get("Content-Type", "").startswith(("text/html", "text/csv")):
                continue
            body = r.content.decode(errors="replace")
            bad += [(url, w) for w in BANNED if w in body]
            queue += [u.replace("&amp;", "&") for u in LINK.findall(body)]
        return seen, bad

    def test_every_reachable_screen_for_each_kind_of_member(self):
        for label, user in (("관리자", self.u4), ("교사", self.u5), ("교무실", self.office_user),
                            ("전담", self.jeon), ("손님", self.guest)):
            with self.subTest(label):
                seen, bad = self.crawl(user)
                self.assertGreater(len(seen), 15, sorted(seen))
                self.assertEqual(bad, [])

    def test_submit_link_screens_have_no_banned_words(self):
        c = Client()
        for url in (f"/c/{self.c5.link_token}", f"/c/{self.c5.link_token}/outside",
                    f"/c/{self.c5.link_token}/u/{self.r5.key}"):
            r = c.get(url)
            self.assertLess(r.status_code, 500, url)
            body = r.content.decode()
            for word in BANNED:
                self.assertNotIn(word, body, url)

    def test_other_school_and_anonymous_get_nothing_from_any_reached_address(self):
        seen, _ = self.crawl(self.u5)
        inner = [u for u in seen if u.startswith(f"/s/{self.hub.token}/")]
        self.assertGreater(len(inner), 15)
        far, anon = Client(), Client()
        far.force_login(self.far_user)
        for url in inner:
            self.assertEqual(far.get(url).status_code, 404, url)
            self.assertEqual(anon.get(url).status_code, 302, url)


@override_settings(ROOT_URLCONF=__name__)
class GateThroughMiddlewareQa(JoinBase):
    """게이트를 진짜 미들웨어·URL 뒤에 두고 본다(test_school은 RequestFactory로 직접 부른다). H2에는 게이트를 쓰는 쓰기 화면이 없다."""

    def setUp(self):
        super().setUp()
        self.w = f"/s/{self.hub.token}/qa-school-write"
        self.mw = f"/s/{self.hub.token}/qa-manager-write"
        self.manager, self.teacher = Client(), Client()
        self.manager.force_login(self.u4)
        self.teacher.force_login(self.u5)

    def expire(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))

    def test_before_expiry_write_passes(self):
        self.assertEqual(self.teacher.post(self.w).status_code, 200)

    def test_expired_post_refused_get_reads(self):
        self.expire()
        self.assertEqual(self.teacher.get(self.w).status_code, 200)
        for call in (self.teacher.post, self.teacher.put, self.teacher.patch, self.teacher.delete):
            self.assertEqual(call(self.w).status_code, 403)

    def test_manager_gate_ignores_position_text(self):
        Member.objects.filter(pk=self.m5.pk).update(position="교장")
        self.assertEqual(self.teacher.post(self.mw).status_code, 403)
        self.assertEqual(self.manager.post(self.mw).status_code, 200)

    def test_expired_manager_also_refused(self):
        self.expire()
        self.assertEqual(self.manager.post(self.mw).status_code, 403)

    def test_grade_space_and_other_school_are_404_guest_of_anonymous_redirects(self):
        far = Client()
        far.force_login(self.far_user)
        self.assertEqual(far.post(f"/s/{self.far.token}/qa-school-write").status_code, 404)
        self.assertEqual(far.post(self.w).status_code, 404)
        self.assertEqual(Client().post(self.w).status_code, 302)


class GuestToGoogleQa(JoinBase):
    def login_via_google(self, c, sub):
        c.get("/auth/google/login", {"next": "/join", "remember": "0"})
        info = {"sub": sub, "email": "a@b.kr", "email_verified": True, "name": "이은구글"}
        with mock.patch("accounts.views._http_json", side_effect=[TOKEN, info]):
            return c.get("/auth/google/callback", {"state": c.session["google_state"], "code": "x"})

    @override_settings(GOOGLE_CLIENT_ID="cid")
    def test_seatless_guest_connects_google_and_then_enters(self):
        """화면 문구 '구글 계정을 이으면 바로 이어져요'. 비밀번호 해시가 바뀌어 login이 세션을 비우고 초대 정보가 사라져 /join이 404가 된다."""
        guest = User.objects.create(username="guest:free", first_name="떠돌이")
        c = Client()
        c.force_login(guest)
        c.get(self.link)
        self.assertEqual(self.login_via_google(c, "free-google")["Location"], "/join")
        self.assertContains(c.get("/join"), 'name="group"')
        r = c.post("/join", {"group": "meal", "role": "영양교사", "name": "이은구글"})
        self.assertRedirects(r, f"/s/{self.hub.token}/welcome", fetch_redirect_response=False)
        seat = Member.objects.in_space(self.hub).get(name="이은구글")
        self.assertEqual(seat.user.username, "google:free-google")

    @override_settings(GOOGLE_CLIENT_ID="cid")
    def test_merged_guest_connecting_google_keeps_one_seat_and_is_google_now(self):
        guest = User.objects.create(username="guest:merged", first_name="옮긴손님")
        seat = Member.objects.create(space=self.hub, user=guest, name="옮긴손님", cls="5-3", group="g5")
        c = Client()
        c.force_login(guest)
        self.login_via_google(c, "merged-google")
        seat.refresh_from_db()
        self.assertEqual(seat.user.username, "google:merged-google")
        self.assertEqual(Member.objects.in_space(self.hub).filter(name="옮긴손님").count(), 1)
        self.assertEqual(c.get(f"/s/{self.hub.token}/settings").status_code, 200)


class JoinInputQa(JoinBase):
    def client_for(self, sub):
        c = Client()
        c.force_login(google(sub))
        c.get(self.link)
        return c

    def test_long_and_hostile_values_fit_the_columns_and_are_escaped(self):
        evil = '<script>alert(1)</script>' + "가" * 100
        c = self.client_for("long")
        r = c.post("/join", {"group": "support", "role": "", "role_custom": evil, "name": evil})
        self.assertEqual(r.status_code, 302)
        seat = Member.objects.in_space(self.hub).get(user__username="google:long")
        self.assertLessEqual(len(seat.name), 40)
        self.assertLessEqual(len(seat.position), 40)
        page = c.get(f"/s/{self.hub.token}/members", headers={"HX-Request": "true"}).content.decode()
        self.assertNotIn("<script>alert", page)

    def test_query_values_are_escaped_in_the_form(self):
        c = self.client_for("q")
        page = c.get("/join", {"group": "office", "name": '"><script>x</script>', "role": '"><b>'}).content.decode()
        self.assertNotIn("<script>x", page)
        self.assertNotIn('"><b>', page)

    def test_cannot_smuggle_cls_of_other_group_or_manager_flag(self):
        c = self.client_for("sm")
        r = c.post("/join", {"group": "office", "role": "교무", "cls": "4-1", "name": "끼움", "is_manager": "on"})
        self.assertEqual(r.status_code, 302)
        seat = Member.objects.in_space(self.hub).get(name="끼움")
        self.assertEqual((seat.cls, seat.group, seat.is_manager), ("교무실", "office", False))

    def test_join_without_opening_link_is_404_and_no_seat(self):
        c = Client()
        c.force_login(google("nolink"))
        self.assertEqual(c.post("/join", {"group": "office", "role": "교무", "name": "링크없음"}).status_code, 404)
        self.assertFalse(Member.objects.filter(name="링크없음").exists())

    def test_reissue_between_form_and_submit_closes_entry(self):
        c = self.client_for("late")
        manager = Client()
        manager.force_login(self.u4)
        manager.post(f"/s/{self.hub.token}/invite")
        self.assertEqual(c.post("/join", {"group": "office", "role": "교무", "name": "늦음"}).status_code, 404)
        self.assertFalse(Member.objects.filter(name="늦음").exists())


class OldAddressQa(JoinBase):
    def test_old_grade_token_school_screens_follow_for_members_only(self):
        c = Client()
        c.force_login(self.u5)
        for tail in ("school", "board"):
            r = c.get(f"/s/{self.g5.token}/{tail}")
            self.assertRedirects(r, f"/s/{self.hub.token}/{tail}", fetch_redirect_response=False)
        far = Client()
        far.force_login(self.far_user)
        self.assertEqual(far.get(f"/s/{self.g5.token}/school").status_code, 404)


class NewGradeQa(JoinBase):
    def pick(self, c):
        s = c.session
        s[views.PICKED] = {"atpt": self.school.neis_atpt, "code": self.school.neis_code, "name": self.school.name,
                           "office": "경기"}
        s.save()

    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(google("ng"))
        self.pick(self.c)

    def test_htmx_grade_pick_and_post_get_only_the_notice(self):
        hx = {"HX-Request": "true"}
        r = self.c.get("/new/class", {"grade": 2}, headers=hx)
        self.assertContains(r, views.SCHOOL_TAKEN)
        self.assertNotContains(r, 'name="cls"')
        self.assertContains(self.c.post("/new/class", {"grade": 2, "cls": "2-1", "name": "만들기"}, headers=hx),
                            views.SCHOOL_TAKEN, status_code=409)
        self.assertFalse(Space.objects.filter(school=self.school, grade=2).exists())

    def test_after_plan_off_still_no_new_grade(self):
        School.objects.filter(pk=self.school.pk).update(plan="free", paid_until=None)
        r = self.c.post("/new/class", {"grade": 2, "cls": "2-1", "name": "만들기"})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(Space.objects.filter(school=self.school, grade=2).exists())

    def test_session_pick_survives_refused_post_so_page_can_reload(self):
        self.c.post("/new/class", {"grade": 2, "cls": "2-1", "name": "만들기"})
        self.assertContains(self.c.get("/new/class"), views.SCHOOL_TAKEN)


class GradeWordsUnchangedQa(MergeBase):
    """H2 전 main과 렌더링을 맞대 본 결과에서 나온 회귀. 동학년 공간 문구는 그대로여야 한다."""

    def settings_html(self, user, space):
        c = Client()
        c.force_login(user)
        return c.get(f"/s/{space.token}/settings?a=space").content.decode()

    def test_last_member_leave_text_keeps_josa(self):
        html = self.settings_html(self.far_user, self.far)
        self.assertNotIn("동학년와", html)
        self.assertIn("이 동학년과 취합이 모두 지워져요", html)

    def test_leave_text_is_the_same_as_before(self):
        html = self.settings_html(self.u4, self.g4)
        self.assertIn("동학년은 다른 선생님들에게 그대로 남아요", html)

    def test_class_with_word_name_still_reads_homeroom(self):
        """나이스 반 이름이 숫자가 아닌 학교(가·나반): 전담 수정 뒤 '담임'이 빈칸이 됐다."""
        Member.objects.create(space=self.g4, user=google("gaban"), name="가반샘", cls="4-가")
        html = self.settings_html(self.u4, self.g4)
        row = html[html.index("가반샘"):]
        self.assertIn('muted">담임</span>', row[:row.index("</div></div>") + 80])
