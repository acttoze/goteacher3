"""G3 S3 회원 관리: 만든 사람의 '내보내기', 손님 자리에 새 기기 알림, 초대 입장 빈도 제한."""
from unittest import mock

from django.core.cache import caches
from django.test import Client, override_settings

from accounts.models import DEVICE_COOKIE, DeviceToken
from collect.models import Cell, Collection, Field, Notification, Row

from .models import Member
from .test_invite import InviteBase, User


class RemoveMemberTests(InviteBase):
    def setUp(self):
        super().setUp()
        self.guest_pc = Client()
        self.join(self.guest_pc, "4-2", "정하은")
        self.guest = Member.objects.get(space=self.space, name="정하은")

    def out(self, client, m):
        return client.post(f"/s/{self.space.token}/members/{m.key}/out", headers={"HX-Request": "true"})

    def test_creator_sees_button_others_do_not(self):
        r = self.client.get(f"/s/{self.space.token}/")
        self.assertContains(r, f"/members/{self.guest.key}/out")
        self.assertContains(r, "내보내기")
        r = self.guest_pc.get(f"/s/{self.space.token}/")
        self.assertNotContains(r, "/out\"")

    def test_creator_removes_guest_drops_tokens_session_keeps_values(self):
        c = Collection.objects.create(space=self.space, title="체험학습", source="paste")
        f = Field.objects.create(collection=c, pos=0, name="인원")
        row = Row.objects.create(collection=c, pos=0, unit="4-2", who="정하은", member=self.guest)
        Cell.objects.create(row=row, field=f, value="24", by="정하은")
        guest_user = self.guest.user
        self.assertTrue(DeviceToken.objects.filter(user=guest_user).exists())
        r = self.out(self.client, self.guest)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "정하은</b>")
        self.assertContains(r, "data-toast-now")
        self.assertFalse(Member.objects.filter(pk=self.guest.pk).exists())
        self.assertFalse(User.objects.filter(pk=guest_user.pk).exists())
        self.assertFalse(DeviceToken.objects.filter(user_id=guest_user.pk).exists())
        # 낸 값은 합본에 남고, 줄은 명부와 끊긴다
        row.refresh_from_db()
        self.assertIsNone(row.member_id)
        self.assertEqual(Cell.objects.get(row=row).value, "24")
        # 내보낸 PC는 세션도 기기 토큰도 소용없다
        self.assertEqual(self.guest_pc.get(f"/s/{self.space.token}/").status_code, 302)

    def test_removing_google_member_keeps_their_account(self):
        u = User.objects.create(username="google:9", first_name="김도윤")
        m = Member.objects.create(space=self.space, user=u, name="김도윤", cls="4-3")
        DeviceToken.issue(u)
        self.assertEqual(self.out(self.client, m).status_code, 200)
        self.assertFalse(Member.objects.filter(pk=m.pk).exists())
        self.assertTrue(User.objects.filter(pk=u.pk).exists())

    def test_only_creator_and_not_self(self):
        self.assertEqual(self.out(self.guest_pc, Member.objects.get(user=self.owner)).status_code, 403)
        self.assertEqual(self.out(self.client, Member.objects.get(user=self.owner)).status_code, 403)
        self.assertTrue(Member.objects.filter(space=self.space).count() == 2)

    def test_other_space_member_is_404(self):
        outsider = Member.objects.create(space=self.other, user=User.objects.create(username="google:7"),
                                         name="남", cls="5-1")
        self.assertEqual(self.out(self.client, outsider).status_code, 404)
        self.assertEqual(self.client.post(f"/s/{self.other.token}/members/{outsider.key}/out").status_code, 404)
        self.assertTrue(Member.objects.filter(pk=outsider.pk).exists())

    def test_needs_post(self):
        self.assertEqual(self.client.get(f"/s/{self.space.token}/members/{self.guest.key}/out").status_code, 405)


class SeatAlertTests(InviteBase):
    def setUp(self):
        super().setUp()
        self.creator = Member.objects.get(user=self.owner)

    def test_new_browser_into_guest_seat_with_device_alerts_creator(self):
        self.join(Client(), "4-2", "정하은")
        self.assertFalse(Notification.objects.exists())
        r = self.join(Client(), "4-2", "정하은")  # 다른 브라우저가 같은 반·이름으로(허용형 유지)
        self.assertEqual(r.status_code, 302)
        n = Notification.objects.get()
        self.assertEqual((n.member, n.space, n.kind), (self.creator, self.space, Notification.Kind.SEAT))
        self.assertEqual(n.text, "2반 정하은 자리로 새 기기가 들어왔어요")
        self.assertEqual(n.link, f"/s/{self.space.token}/")
        r = self.client.get(f"/s/{self.space.token}/bell")
        self.assertContains(r, "2반 정하은 자리로 새 기기가 들어왔어요")
        self.assertContains(r, "새 기기")

    def test_one_alert_per_seat_per_day(self):
        self.join(Client(), "4-2", "정하은")
        for _ in range(3):
            self.join(Client(), "4-2", "정하은")
        self.assertEqual(Notification.objects.count(), 1)
        self.join(Client(), "4-3", "김도윤")
        self.join(Client(), "4-3", "김도윤")
        self.assertEqual(Notification.objects.count(), 2)  # 다른 자리는 따로

    def test_no_alert_when_seat_has_no_device(self):
        pc = Client()
        self.join(pc, "4-2", "정하은")
        DeviceToken.objects.all().delete()
        self.join(Client(), "4-2", "정하은")
        self.assertFalse(Notification.objects.exists())

    def test_first_join_no_alert(self):
        self.join(Client(), "4-3", "김도윤")
        self.assertFalse(Notification.objects.exists())


RL_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "ratelimit": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "rl-join-test"},
}


@override_settings(CACHES=RL_CACHES)
class JoinRateLimitTests(InviteBase):
    def setUp(self):
        super().setUp()
        caches["ratelimit"].clear()
        # 고정 창(분 단위)이라 테스트 도중 분이 바뀌면 카운터가 새로 시작한다 → 시각을 고정
        clock = mock.patch("config.ratelimit.time.time", return_value=1_800_000_030.0)
        clock.start()
        self.addCleanup(clock.stop)

    def test_session_limit_per_minute(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        codes = [c.post("/join", {"cls": "", "name": f"가짜{i}"}).status_code for i in range(6)]
        self.assertEqual(codes[:5], [400] * 5)
        self.assertEqual(codes[5], 429)
        r = c.post("/join", {"cls": "4-2", "name": "정하은"})
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "1분 뒤", status_code=429)
        self.assertFalse(Member.objects.filter(name="정하은").exists())

    def test_ip_limit_per_minute_across_sessions(self):
        codes = []
        for i in range(11):
            c = Client(REMOTE_ADDR="203.0.113.5")
            c.get(f"/j/{self.space.invite_token}")
            codes.append(c.post("/join", {"cls": "", "name": f"가짜{i}"}).status_code)
        self.assertEqual(codes[-1], 429)
        self.assertNotIn(429, codes[:10])
        # 다른 학교(다른 IP)는 그대로
        c = Client(REMOTE_ADDR="198.51.100.7")
        c.get(f"/j/{self.space.invite_token}")
        self.assertEqual(c.post("/join", {"cls": "4-2", "name": "정하은"}).status_code, 302)

    def test_forwarded_for_uses_last_hop(self):
        for i in range(11):
            c = Client(REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR=f"1.1.1.{i}, 203.0.113.9")
            c.get(f"/j/{self.space.invite_token}")
            code = c.post("/join", {"cls": "", "name": "x"}).status_code
        self.assertEqual(code, 429)  # 앞쪽(위조 가능) 값이 달라도 마지막(Traefik이 붙인) 값으로 센다

    def test_get_is_not_limited(self):
        c = Client()
        c.get(f"/j/{self.space.invite_token}")
        self.assertEqual({c.get("/join").status_code for _ in range(12)}, {200})
        self.assertIn(DEVICE_COOKIE, self.join(c, "4-2", "정하은").cookies)


class NoStoreAndNeisLimitTests(InviteBase):
    def test_space_screens_and_start_are_no_store(self):
        t = self.space.token
        for url in [f"/s/{t}/", f"/s/{t}/members", f"/s/{t}/made", f"/s/{t}/welcome", f"/s/{t}/settings", "/"]:
            self.assertIn("no-store", self.client.get(url).get("Cache-Control", ""), url)
        c = Client()
        self.assertIn("no-store", c.get(f"/j/{self.space.invite_token}")["Cache-Control"])
        self.assertIn("no-store", c.get("/join")["Cache-Control"])
        self.assertIn("no-store", c.get("/start")["Cache-Control"])

    def test_invite_and_out_buttons_are_40px(self):
        Member.objects.create(space=self.space, user=User.objects.create(username="google:8"), name="김", cls="4-3")
        r = self.client.get(f"/s/{self.space.token}/")
        self.assertContains(r, "min-height:var(--control-h-sm)", count=2)


@override_settings(CACHES=RL_CACHES)
class NeisSearchLimitTests(InviteBase):
    def setUp(self):
        super().setUp()
        caches["ratelimit"].clear()
        Member.objects.filter(user=self.owner).delete()  # 동학년 없는 구글 회원: 학교 찾기 화면

    def test_twenty_per_minute_per_user(self):
        from unittest import mock
        with mock.patch("spaces.neis.search_schools", return_value=([], 0)) as m:
            codes = [self.client.get("/", {"q": f"새솔{i}"}).status_code for i in range(21)]
        self.assertEqual(codes[:20], [200] * 20)
        self.assertEqual(codes[20], 429)
        self.assertEqual(m.call_count, 20)
        r = self.client.get("/", {"q": "새솔"}, headers={"HX-Request": "true"})
        self.assertContains(r, "1분 뒤", status_code=429)


class RemoveDialogHandoverTests(InviteBase):
    """내보내기 확인창: 그 선생님이 만든 모으기 수(서버가 셈)를 N>0일 때만 알린다. 다른 공간 것은 세지 않는다."""

    def setUp(self):
        super().setUp()
        self.guest_pc = Client()
        self.join(self.guest_pc, "4-2", "정하은")
        self.guest = Member.objects.get(space=self.space, name="정하은")

    def test_count_shown_only_when_positive(self):
        r = self.client.get(f"/s/{self.space.token}/")
        self.assertNotContains(r, "내가 넘겨받아요")
        for t in ("가", "나"):
            Collection.objects.create(space=self.space, title=t, source="paste", created_by=self.guest)
        Collection.objects.create(space=self.space, title="내 것", source="paste",
                                  created_by=Member.objects.get(user=self.owner))
        outsider = Member.objects.create(space=self.other, name="남", cls="5-1")
        Collection.objects.create(space=self.other, title="남의 것", source="paste", created_by=outsider)
        r = self.client.get(f"/s/{self.space.token}/members")
        self.assertContains(r, "이 선생님이 만든 모으기 2개는 내가 넘겨받아요", count=1)

    def test_not_shown_to_non_creator(self):
        Collection.objects.create(space=self.space, title="가", source="paste", created_by=self.guest)
        r = self.guest_pc.get(f"/s/{self.space.token}/members")
        self.assertNotContains(r, "넘겨받아요")
