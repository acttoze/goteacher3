"""G3 보안 고침: 받는 링크 새로 만들기(S5), 캐시 금지(S6), 받는 사람 제출·명부 밖 줄 빈도 제한(⑥).

불변식
- 받는 링크 새로 만들기는 만든 사람만. 다른 회원 403, 다른 공간 404, GET 405, CSRF 필요.
  새로 만들면 옛 링크(줄 화면 포함)는 404, 새 링크로 낸다. 카드·쪽지 문구·마감 안내·🔔 알림도 새 링크.
- 받는 사람 화면(/c/… 전부)과 취합 만들기 화면은 Cache-Control: no-store.
- 같은 IP가 같은 링크로 분당 120번 넘게 내면 429와 안내 문구, 값은 저장하지 않는다. 다른 IP·다른 링크는 따로 센다.
  오류 난 제출(400)은 세지 않는다(한 학교가 IP 하나를 같이 쓴다).
- 명부 밖 줄 만들기도 같은 IP·링크로 시간당 60번까지.
- 만든 사람이 없는 취합(내보낸 회원의 것)은 동학년을 만든 사람(가장 먼저 들어온 회원)이 관리한다.
"""
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from config.test_ratelimit import RL_CACHES
from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Notification, Row
from .submit import OUTSIDE_PER_HOUR, SUBMIT_PER_MIN


@override_settings(SITE_URL="https://t.goteacher.xyz")
class G3Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.owner, title="학예회 참가 인원",
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"],
                                          allow_outside=True)
        cls.f = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])


class ReissueLinkTests(G3Base):
    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, c=None, space=None):
        c = c or self.c
        return f"/s/{(space or c.space).token}/c/{c.key}/link"

    def test_owner_reissues_and_old_link_dies(self):
        old = self.c.link_token
        r = self.client.post(self.url(), HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.c.refresh_from_db()
        new = self.c.link_token
        self.assertNotEqual(new, old)
        self.assertGreaterEqual(len(new), 32)
        anon = Client()
        self.assertEqual(anon.get(f"/c/{old}").status_code, 404)
        self.assertEqual(anon.get(f"/c/{old}/u/{self.r2.key}").status_code, 404)
        self.assertEqual(anon.post(f"/c/{old}/u/{self.r2.key}", {"f0": "3"}).status_code, 404)
        self.assertEqual(anon.get(f"/c/{new}").status_code, 200)
        self.assertEqual(anon.post(f"/c/{new}/u/{self.r2.key}", {"f0": "3", "by": "박서준"}).status_code, 200)
        # 카드 링크·쪽지 문구가 새 링크, 옛 링크는 화면 어디에도 없다
        self.assertContains(r, f'id="lk" readonly value="https://t.goteacher.xyz/c/{new}"')
        self.assertRegex(r.content.decode(), rf'id="msg">[^<]*https://t.goteacher.xyz/c/{new}')
        self.assertNotContains(r, old)
        self.assertContains(r, "새 링크를 만들었어요")

    def test_notice_and_bell_follow_new_link(self):
        self.c.due = timezone.now() + timezone.timedelta(days=2)
        self.c.save(update_fields=["due"])
        n = Notification.objects.create(space=self.a, member=self.m2, row=self.r2, kind="d1",
                                        day=timezone.localdate(), text="학예회", link=f"/c/{self.c.link_token}/u/{self.r2.key}")
        old = self.c.link_token
        r = self.client.post(self.url(), HTTP_HX_REQUEST="true")
        self.c.refresh_from_db()
        self.assertNotContains(r, old)
        self.assertRegex(r.content.decode(), rf'id="notice">[^<]*/c/{self.c.link_token}')  # 마감 안내 문구
        n.refresh_from_db()
        self.assertEqual(n.link, f"/c/{self.c.link_token}/u/{self.r2.key}")

    def test_without_htmx_redirects_to_detail(self):
        r = self.client.post(self.url())
        self.assertRedirects(r, f"/s/{self.a.token}/c/{self.c.key}/", fetch_redirect_response=False)

    def test_detail_button_owner_only(self):
        r = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/")
        self.assertContains(r, f'hx-post="{self.url()}"')
        self.assertContains(r, "링크 새로 만들기")
        self.assertContains(r, "hx-confirm=")
        c2 = Client()
        c2.force_login(self.u2)
        r = c2.get(f"/s/{self.a.token}/c/{self.c.key}/")
        self.assertNotContains(r, "링크 새로 만들기")

    def test_other_member_forbidden(self):
        c2 = Client()
        c2.force_login(self.u2)
        old = self.c.link_token
        self.assertEqual(c2.post(self.url()).status_code, 403)
        self.c.refresh_from_db()
        self.assertEqual(self.c.link_token, old)

    def test_other_space_404(self):
        self.assertEqual(self.client.post(self.url(self.other, self.a)).status_code, 404)
        self.assertEqual(self.client.post(self.url(self.c, self.b)).status_code, 404)

    def test_get_not_allowed_and_csrf(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.ua)
        self.assertEqual(strict.post(self.url()).status_code, 403)


class NoStoreTests(G3Base):
    def assertNoStore(self, r):
        self.assertIn("no-store", r.get("Cache-Control", ""), r.status_code)

    def test_recipient_screens(self):
        lt = self.c.link_token
        self.assertNoStore(self.client.get(f"/c/{lt}"))
        self.assertNoStore(self.client.get(f"/c/{lt}/u/{self.r2.key}"))
        self.assertNoStore(self.client.post(f"/c/{lt}/u/{self.r2.key}", {"f0": "3", "by": "박서준"}))
        self.assertNoStore(self.client.post(f"/c/{lt}/u/{self.r2.key}", {"f0": "", "by": "박서준"}))
        self.assertNoStore(self.client.post(f"/c/{lt}/outside", {"name": "강사"}, follow=True))
        self.assertNoStore(self.client.get("/c/없는링크"))

    def test_collect_member_screens(self):
        self.client.force_login(self.ua)
        t = self.a.token
        self.assertNoStore(self.client.get(f"/s/{t}/c/new"))
        self.assertNoStore(self.client.post(f"/s/{t}/c/new/preview", {"paste": "반\t담임\t참가\n4-1\t\t\n"}))
        self.assertNoStore(self.client.post(f"/s/{t}/c/{self.c.key}/lock", HTTP_HX_REQUEST="true"))
        self.assertNoStore(self.client.post(f"/s/{t}/c/{self.c.key}/due", {"date": "2026-12-01", "time": "17:00"}))
        self.assertNoStore(self.client.post(f"/s/{t}/c/{self.c.key}/footer", {"on": "on"}))


@override_settings(CACHES=RL_CACHES)
class SubmitRateTests(G3Base):
    def setUp(self):
        caches["ratelimit"].clear()
        clock = mock.patch("config.ratelimit.time.time", return_value=1_800_000_030.0)  # 고정 창이 테스트 중 바뀌지 않게
        clock.start()
        self.addCleanup(clock.stop)

    def send(self, ip, row=None, c=None, value="3"):
        c = c or self.c
        return self.client.post(f"/c/{c.link_token}/u/{(row or self.r2).key}", {"f0": value, "by": "박서준"},
                                REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR=ip, HTTP_HX_REQUEST="true")

    def test_submit_limit_per_ip_and_link(self):
        self.assertEqual((SUBMIT_PER_MIN, OUTSIDE_PER_HOUR), (120, 60))
        for _ in range(SUBMIT_PER_MIN):
            self.assertEqual(self.send("203.0.113.9").status_code, 200)
        r = self.send("203.0.113.9", value="99")
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "잠시 뒤 다시", status_code=429)
        self.assertEqual(Cell.objects.get(row=self.r2, field=self.f).value, "3")  # 막힌 값은 저장 안 함
        self.assertEqual(self.send("198.51.100.7").status_code, 200)  # 다른 IP는 따로
        # 같은 IP여도 앞에 가짜 주소를 넣어 우회할 수 없다
        self.assertEqual(self.send("1.1.1.1, 203.0.113.9").status_code, 429)

    def test_errors_not_counted(self):
        for _ in range(SUBMIT_PER_MIN + 5):
            self.assertEqual(self.send("203.0.113.9", value="많이").status_code, 400)  # 숫자 칸에 글자
        self.assertEqual(self.send("203.0.113.9").status_code, 200)

    def test_get_not_limited(self):
        for _ in range(SUBMIT_PER_MIN + 1):
            self.send("203.0.113.9")
        r = self.client.get(f"/c/{self.c.link_token}/u/{self.r2.key}", HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(r.status_code, 200)

    def test_outside_limit(self):
        url = f"/c/{self.c.link_token}/outside"
        for i in range(OUTSIDE_PER_HOUR):
            r = self.client.post(url, {"name": f"강사{i}"}, HTTP_X_FORWARDED_FOR="203.0.113.9")
            self.assertEqual(r.status_code, 303)
        r = self.client.post(url, {"name": "강사99"}, HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "잠시 뒤 다시", status_code=429)
        self.assertFalse(self.c.rows.filter(who="강사99").exists())


class OrphanCollectionTests(G3Base):
    """내보낸 구글 회원이 만든 취합: 동학년을 만든 사람이 넘겨받는다(점검표 내보내기 권한 낮음 1)."""

    def detail(self, client, c):
        return client.get(f"/s/{self.a.token}/c/{c.key}/")

    def test_removed_creator_hands_over_to_space_creator(self):
        c = Collection.objects.create(space=self.a, created_by=self.m2, title="박 선생님 취합", source="sheet",
                                      unit_head=["학년반", "담임"])
        self.client.force_login(self.ua)
        self.assertNotContains(self.detail(self.client, c), "링크 새로 만들기")
        r = self.client.post(f"/s/{self.a.token}/members/{self.m2.key}/out", HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.created_by, self.owner)
        self.assertContains(self.detail(self.client, c), "링크 새로 만들기")
        self.assertEqual(self.client.post(f"/s/{self.a.token}/c/{c.key}/link").status_code, 302)

    def test_already_orphaned_goes_to_space_creator_only(self):
        c = Collection.objects.create(space=self.a, created_by=None, title="주인 없음", source="sheet",
                                      unit_head=["학년반", "담임"])
        c2 = Client()
        c2.force_login(self.u2)
        self.assertEqual(c2.post(f"/s/{self.a.token}/c/{c.key}/link").status_code, 403)
        self.client.force_login(self.ua)
        self.assertEqual(self.client.post(f"/s/{self.a.token}/c/{c.key}/link").status_code, 302)
        # 다른 공간의 주인 없는 취합은 404(공간 격리)
        orphan_b = Collection.objects.create(space=self.b, created_by=None, title="옆", source="sheet",
                                             unit_head=["학년반", "담임"])
        self.assertEqual(self.client.post(f"/s/{self.a.token}/c/{orphan_b.key}/link").status_code, 404)
