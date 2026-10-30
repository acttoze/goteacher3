"""B7 초대 링크 → 가입 없이 입장. NEIS는 목(5개 반)."""
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import DEVICE_COOKIE, DeviceToken, is_guest

from .models import Member, School, Space

User = get_user_model()
SITE = "https://t.goteacher.xyz"


@override_settings(SITE_URL=SITE, GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="sec")
class InviteBase(TestCase):
    def setUp(self):
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2", "3", "4", "5"])
        p.start()
        self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.space = Space.objects.create(school=school, grade=4, name="새솔초 4학년")
        self.other = Space.objects.create(school=school, grade=5, name="새솔초 5학년")
        self.owner = User.objects.create(username="google:1", first_name="송민재")
        Member.objects.create(space=self.space, user=self.owner, name="송민재", cls="4-1")
        self.client.force_login(self.owner)

    def join(self, client, cls, name, token=None):
        r = client.get(f"/j/{token or self.space.invite_token}")
        self.assertEqual((r.status_code, r["Location"]), (302, "/join"))
        return client.post("/join", {"cls": cls, "name": name})


class JoinFlowTests(InviteBase):
    def test_invite_link_becomes_session_and_redirects_without_token(self):
        c = Client()
        r = c.get(f"/j/{self.space.invite_token}")
        self.assertEqual(r["Location"], "/join")
        r = c.get("/join")
        self.assertContains(r, "새솔초 4학년 동학년에")
        self.assertContains(r, "송민재")
        self.assertNotContains(r, self.space.invite_token)
        self.assertNotContains(r, self.space.token)

    def test_five_teachers_join_and_owner_poll_shows_them(self):
        names = [("4-2", "정하은"), ("4-3", "김도윤"), ("4-4", "이수빈"), ("4-5", "박서준"), ("4-전담", "노태윤")]
        for cls, name in names:
            c = Client()
            r = self.join(c, cls, name)
            self.assertEqual(r["Location"], f"/s/{self.space.token}/welcome")
            self.assertIn(DEVICE_COOKIE, r.cookies)
            self.assertContains(c.get(r["Location"]), f"들어왔어요, {name} 선생님")
        r = self.client.get(f"/s/{self.space.token}/members", headers={"HX-Request": "true"})
        self.assertNotContains(r, "<html")
        for _, name in names:
            self.assertContains(r, name)
        self.assertContains(r, 'hx-trigger="every 5s"')
        self.assertContains(r, "6</b>")  # 6/6칸
        self.assertContains(r, "초대 링크 · 이름만", count=5)
        self.assertEqual(Member.objects.in_space(self.space).count(), 6)
        guests = User.objects.filter(members__space=self.space).exclude(pk=self.owner.pk)
        self.assertTrue(all(is_guest(u) for u in guests))

    def test_guest_reopens_browser_with_device_cookie_only(self):
        c = Client()
        r = self.join(c, "4-2", "정하은")
        raw = r.cookies[DEVICE_COOKIE].value
        fresh = Client()
        fresh.cookies[DEVICE_COOKIE] = raw
        r = fresh.get(f"/s/{self.space.token}/")
        self.assertContains(r, "정하은 (나)")
        self.assertEqual(fresh.get("/")["Location"], f"/s/{self.space.token}/")
        self.assertNotEqual(DeviceToken.objects.get().token_hash, raw)  # 해시만 저장

    def test_name_is_cleaned_and_required(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        r = c.post("/join", {"cls": "4-2", "name": " ‮​ "})
        self.assertContains(r, "동학년에게 보일 이름을 써 주세요", status_code=400)
        r = c.post("/join", {"cls": "4-9", "name": "정하은"})
        self.assertContains(r, "몇 반인지 골라 주세요", status_code=400)
        self.join(c, "4-2", "정‮하은\u0000")
        self.assertTrue(Member.objects.filter(space=self.space, name="정하은").exists())

    def test_join_without_invite_session_is_404(self):
        r = Client().get("/join")
        self.assertContains(r, "초대 링크를 찾을 수 없어요", status_code=404)
        r = Client().post("/join", {"cls": "4-2", "name": "정하은"})
        self.assertEqual(r.status_code, 404)

    def test_join_page_fits_body_and_buttons(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        r = c.get("/join")
        self.assertContains(r, 'class="btn pri lg"')
        self.assertContains(r, "가입·비밀번호 없음")
        self.assertNotContains(r, "티로드")


class DuplicateTests(InviteBase):
    def test_same_class_and_name_reuses_member_on_second_pc(self):
        a, b = Client(), Client()
        self.join(a, "4-2", "정하은")
        self.join(b, "4-2", "정하은")
        self.assertEqual(Member.objects.in_space(self.space).filter(name="정하은").count(), 1)
        self.assertEqual(a.get(f"/s/{self.space.token}/").status_code, 200)
        self.assertContains(b.get(f"/s/{self.space.token}/"), "정하은 (나)")

    def test_same_class_other_name_is_new_member(self):
        self.join(Client(), "4-2", "정하은")
        self.join(Client(), "4-2", "최유나")
        self.assertEqual(Member.objects.in_space(self.space).filter(cls="4-2").count(), 2)

    def test_google_member_name_cannot_be_taken_by_link(self):
        r = self.join(Client(), "4-1", "송민재")
        self.assertContains(r, "구글 계정으로 이어 둔 선생님", status_code=400)
        self.assertEqual(Member.objects.in_space(self.space).count(), 1)

    def test_taken_slot_shows_who(self):
        self.join(Client(), "4-2", "정하은")
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        r = c.get("/join")
        self.assertContains(r, "정하은 · 들어옴")
        self.assertContains(r, "아직 아무도 없어요", count=4)  # 3·4·5반·전담


class TokenTests(InviteBase):
    def test_reissue_closes_old_link_and_session(self):
        old = self.space.invite_token
        waiting = Client()
        waiting.get(f"/j/{old}")
        r = self.client.post(f"/s/{self.space.token}/invite", headers={"HX-Request": "true"})
        self.space.refresh_from_db()
        self.assertNotEqual(self.space.invite_token, old)
        self.assertContains(r, f"{SITE}/j/{self.space.invite_token}")
        self.assertContains(Client().get(f"/j/{old}"), "초대 링크를 찾을 수 없어요", status_code=404)
        self.assertEqual(waiting.post("/join", {"cls": "4-2", "name": "정하은"}).status_code, 404)
        self.join(Client(), "4-2", "정하은")  # 새 링크는 된다

    def test_only_creator_reissues(self):
        c = Client()
        self.join(c, "4-2", "정하은")
        old = self.space.invite_token
        self.assertEqual(c.post(f"/s/{self.space.token}/invite").status_code, 403)
        self.space.refresh_from_db()
        self.assertEqual(self.space.invite_token, old)
        self.assertNotContains(c.get(f"/s/{self.space.token}/"), "링크 새로 만들기")
        self.assertContains(self.client.get(f"/s/{self.space.token}/"), "링크 새로 만들기")

    def test_reissue_needs_post_and_csrf(self):
        self.assertEqual(self.client.get(f"/s/{self.space.token}/invite").status_code, 405)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        self.assertEqual(c.post(f"/s/{self.space.token}/invite").status_code, 403)

    def test_invite_token_is_not_a_space_address_and_back(self):
        self.assertEqual(self.client.get(f"/s/{self.space.invite_token}/").status_code, 404)
        self.assertContains(Client().get(f"/j/{self.space.token}"), "초대 링크를 찾을 수 없어요", status_code=404)

    def test_tokens_are_long(self):
        self.assertGreaterEqual(len(self.space.invite_token), 32)
        self.join(Client(), "4-2", "정하은")
        guest = User.objects.get(members__name="정하은")
        self.assertGreaterEqual(len(guest.username), len("guest:") + 22)
        self.assertFalse(guest.has_usable_password())


class IsolationTests(InviteBase):
    def test_guest_cannot_see_other_space(self):
        c = Client()
        self.join(c, "4-2", "정하은")
        for url in ("/", "members", "welcome", "made"):
            path = f"/s/{self.other.token}/{'' if url == '/' else url}"
            self.assertEqual(c.get(path).status_code, 404, path)
        self.assertEqual(c.post(f"/s/{self.other.token}/invite").status_code, 404)

    def test_anonymous_poll_goes_to_login(self):
        r = Client().get(f"/s/{self.space.token}/members")
        self.assertTrue(r["Location"].startswith("/?next="))
        r = Client().get(f"/s/{self.space.token}/members", headers={"HX-Request": "true"})
        self.assertEqual((r.status_code, r["HX-Redirect"]), (204, "/"))

    def test_member_of_other_space_is_told_and_blocked(self):
        u = User.objects.create(username="google:5", first_name="이나래")
        Member.objects.create(space=self.other, user=u, name="이나래", cls="5-1")
        c = Client()
        c.force_login(u)
        c.get(f"/j/{self.space.invite_token}")
        r = c.get("/join")
        self.assertContains(r, "이미 새솔초 5학년 동학년에 들어가 있어요")
        self.assertNotContains(r, 'name="cls"')
        c.post("/join", {"cls": "4-2", "name": "이나래"})
        self.assertFalse(Member.objects.in_space(self.space).filter(user=u).exists())

    def test_member_opening_own_invite_goes_to_space(self):
        self.client.get(f"/j/{self.space.invite_token}")
        self.assertEqual(self.client.get("/join")["Location"], f"/s/{self.space.token}/")

    def test_google_user_without_space_joins_as_self(self):
        u = User.objects.create(username="google:6", first_name="정하은")
        c = Client()
        c.force_login(u)
        self.join(c, "4-2", "정하은")
        self.assertEqual(Member.objects.get(space=self.space, name="정하은").user, u)
        self.assertContains(self.client.get(f"/s/{self.space.token}/members"), "구글 가입")

    def test_guest_cannot_create_space(self):
        c = Client()
        self.join(c, "4-2", "정하은")
        Member.objects.filter(name="정하은").delete()  # 소속이 없어진 손님
        self.assertContains(c.get("/"), "구글 계정으로 시작하기")
        self.assertEqual(c.post("/new/class", {"grade": 4, "cls": "4-1", "name": "x"})["Location"], "/")
        self.assertEqual(Space.objects.count(), 2)


class OwnerScreenTests(InviteBase):
    def test_invite_card_link_message_and_poll(self):
        r = self.client.get(f"/s/{self.space.token}/")
        link = f"{SITE}/j/{self.space.invite_token}"
        self.assertContains(r, f'value="{link}"')
        self.assertContains(r, "[고티처] 새솔초등학교 4학년 동학년방이에요.")
        self.assertContains(r, "— 송민재 (4학년 1반)")
        self.assertContains(r, f'hx-get="/s/{self.space.token}/members"')
        self.assertContains(r, "쪽지 문구 복사")

    def test_made_page_after_create(self):
        r = self.client.get(f"/s/{self.space.token}/made")
        self.assertContains(r, "새솔초 4학년 동학년을 만들었어요")
        self.assertContains(r, "4단계 중 4단계")
        self.assertContains(r, f"{SITE}/j/{self.space.invite_token}")


@override_settings(SITE_URL=SITE, GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="sec")
class GoogleLinkTests(InviteBase):
    def link(self, c, sub):
        r = c.get("/auth/google/login", {"next": f"/s/{self.space.token}/welcome"})
        state = parse_qs(urlparse(r["Location"]).query)["state"][0]
        info = {"sub": sub, "email": "a@b.kr", "email_verified": True, "name": "정하은G"}
        with mock.patch("accounts.views._http_json", side_effect=[{"access_token": "t"}, info]):
            return c.get("/auth/google/callback", {"state": state, "code": "x"})

    def test_guest_links_google_keeps_member(self):
        c = Client()
        self.join(c, "4-2", "정하은")
        guest = User.objects.get(members__name="정하은")
        r = self.link(c, "777")
        self.assertEqual(r["Location"], f"/s/{self.space.token}/welcome")
        guest.refresh_from_db()
        self.assertEqual(guest.username, "google:777")
        self.assertContains(c.get(r["Location"]), "구글 계정으로 이어 뒀어요")
        self.assertContains(self.client.get(f"/s/{self.space.token}/members"), "구글 가입")

    def test_guest_merges_into_existing_google_user(self):
        existing = User.objects.create(username="google:888")
        c = Client()
        self.join(c, "4-2", "정하은")
        self.link(c, "888")
        m = Member.objects.get(space=self.space, name="정하은")
        self.assertEqual(m.user, existing)
        self.assertFalse(User.objects.filter(username__startswith="guest:").exists())

    def test_google_account_in_other_space_is_refused(self):
        u = User.objects.create(username="google:999")
        Member.objects.create(space=self.other, user=u, name="정하은", cls="5-1")
        c = Client()
        self.join(c, "4-2", "정하은")
        r = self.link(c, "999")
        self.assertContains(r, "이미 다른 동학년", status_code=409)
        self.assertTrue(is_guest(Member.objects.get(space=self.space, name="정하은").user))


class QAEdgeTests(InviteBase):
    """QA(B7 검증)에서 더한 경계 사례."""

    def test_reissue_keeps_joined_guest_on_device_cookie(self):
        r = self.join(Client(), "4-2", "정하은")
        fresh = Client()
        fresh.cookies[DEVICE_COOKIE] = r.cookies[DEVICE_COOKIE].value
        self.client.post(f"/s/{self.space.token}/invite")
        self.assertContains(fresh.get(f"/s/{self.space.token}/"), "정하은 (나)")

    def test_member_of_other_school_gets_404_everywhere(self):
        school = School.objects.create(neis_atpt="B10", neis_code="7000001", name="다른초등학교")
        far = Space.objects.create(school=school, grade=4, name="다른초 4학년")
        u = User.objects.create(username="google:7", first_name="남")
        Member.objects.create(space=far, user=u, name="남", cls="4-1")
        c = Client()
        c.force_login(u)
        for tail in ("", "members", "welcome", "made"):
            self.assertEqual(c.get(f"/s/{self.space.token}/{tail}").status_code, 404, tail)
        self.assertEqual(c.post(f"/s/{self.space.token}/invite").status_code, 404)
        self.assertEqual(c.get(f"/s/{self.space.token}/members", headers={"HX-Request": "true"}).status_code, 404)

    def test_neis_empty_falls_back_to_four_classes(self):
        with mock.patch("spaces.neis.class_names", return_value=[]):
            c = Client()
            c.get(f"/j/{self.space.invite_token}")
            r = c.get("/join")
            for v in ("4-1", "4-2", "4-3", "4-4", "4-전담"):
                self.assertContains(r, f'data-v="{v}"')
            self.assertNotContains(r, 'data-v="4-5"')
            self.assertEqual(c.post("/join", {"cls": "4-5", "name": "박서준"}).status_code, 400)
            self.assertEqual(c.post("/join", {"cls": "4-전담", "name": "박서준"}).status_code, 302)


class SecurityB7Tests(InviteBase):
    """gt-security 차단 1·2, 권고 3."""

    link = GoogleLinkTests.link

    def test_linking_google_revokes_other_guest_devices_and_sessions(self):
        b, a = Client(), Client()
        self.join(b, "4-2", "정하은")
        r = self.join(a, "4-2", "정하은")  # A가 B의 반·이름으로 같은 손님에 들어옴
        a_raw = r.cookies[DEVICE_COOKIE].value
        self.link(b, "777")
        self.assertEqual(a.get(f"/s/{self.space.token}/").status_code, 302)  # 세션 끊김
        fresh = Client()
        fresh.cookies[DEVICE_COOKIE] = a_raw
        self.assertEqual(fresh.get(f"/s/{self.space.token}/").status_code, 302)  # 기기 쿠키도 끊김
        self.assertEqual(b.get(f"/s/{self.space.token}/").status_code, 200)
        self.assertEqual(DeviceToken.objects.count(), 1)

    def test_merging_into_existing_google_does_not_move_guest_devices(self):
        existing = User.objects.create(username="google:888")
        b, a = Client(), Client()
        self.join(b, "4-2", "정하은")
        a_raw = self.join(a, "4-2", "정하은").cookies[DEVICE_COOKIE].value
        self.link(b, "888")
        self.assertEqual(list(DeviceToken.objects.values_list("user", flat=True)), [existing.pk])
        fresh = Client()
        fresh.cookies[DEVICE_COOKIE] = a_raw
        self.assertEqual(fresh.get(f"/s/{self.space.token}/").status_code, 302)
        self.assertEqual(a.get(f"/s/{self.space.token}/").status_code, 302)

    def test_google_user_cannot_take_guest_seat_by_name(self):
        self.join(Client(), "4-2", "정하은")
        guest = Member.objects.get(name="정하은").user
        u = User.objects.create(username="google:6")
        c = Client()
        c.force_login(u)
        r = self.join(c, "4-2", "정하은")
        self.assertContains(r, "이미 들어온 선생님이 있어요", status_code=400)
        self.assertEqual(Member.objects.get(name="정하은").user, guest)
        self.assertFalse(Member.objects.filter(user=u).exists())

    def test_member_cap_is_twice_the_slots(self):
        for i in range(11):  # 만든 사람 + 11 = 12 = 6칸 × 2
            self.join(Client(), "4-전담", f"전담{i}")
        r = self.join(Client(), "4-전담", "넘침")
        self.assertContains(r, "더 들어올 수 없어요", status_code=400)
        self.assertEqual(Member.objects.in_space(self.space).count(), 12)
        self.join(Client(), "4-전담", "전담3")  # 이미 있는 손님 자리는 다시 이어진다
        self.assertEqual(Member.objects.in_space(self.space).count(), 12)

    def test_invite_card_has_server_qr_of_link(self):
        r = self.client.get(f"/s/{self.space.token}/")
        self.assertContains(r, '<img class="qr-img" src="data:image/svg+xml')
        self.assertNotContains(r, "QR 준비 중")
