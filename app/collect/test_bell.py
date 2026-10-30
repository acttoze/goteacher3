"""D3 자동 리마인드(마감 D-1·당일 08:20, 🔔에 쌓기).

불변식
- 관리 명령 remind는 "보낼 때가 된 것만, 한 번씩만": 마감 전날 08:20 이후 첫 실행에 D-1, 당일 08:20 이후 첫 실행에 당일.
  cron이 5분마다 불러도 중복 없음(줄·종류·날짜로 유일), 늦게 불려도 그날 안이면 보낸다. 시간대 Asia/Seoul.
- 대상은 아직 안 낸 줄의 회원(submit._units). 잠근·마감 지난·마감 없는 모으기는 뺀다. 명부 밖 줄(회원 없음)도 뺀다.
- 🔔은 내 알림만. 다른 공간·다른 사람 알림은 404. 읽음은 POST(CSRF), 누르면 그 줄의 제출 화면으로. 캐시 금지.
- 문구에 제출 값은 넣지 않는다(제목·마감 시각만).
"""
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase

from spaces.models import Member, School, Space

from .models import Collection, Field, Notification, Row
from .submit import save_cell

SEOUL = ZoneInfo("Asia/Seoul")


def at(day, hh, mm):
    return datetime(2026, 11, day, hh, mm, tzinfo=SEOUL)


def run(now):
    with mock.patch("django.utils.timezone.now", return_value=now):
        call_command("remind", verbosity=0)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        # 마감 11/5(목) 17:00 → D-1은 11/4 08:20부터, 당일은 11/5 08:20부터
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.owner, title="학예회 참가 인원",
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"],
                                          due=at(5, 17, 0))
        cls.f = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        cls.r3 = Row.objects.create(collection=cls.c, pos=2, unit="4-3", who="이수빈")  # 명부 밖(회원 없음)
        cls.cb = Collection.objects.create(space=cls.b, title="옆 학년 조사", source=Collection.Source.SHEET,
                                           unit_head=["학년반", "담임"], due=at(5, 17, 0))
        Field.objects.create(collection=cls.cb, pos=0, name="인원", kind="number")
        cls.rb = Row.objects.create(collection=cls.cb, pos=0, unit="5-1", who="박다온", member=cls.mb)


class RemindCommandTests(Base):

    def kinds(self, member):
        return sorted(Notification.objects.filter(member=member).values_list("kind", flat=True))

    def test_before_0820_nothing(self):
        run(at(4, 7, 59))
        run(at(4, 8, 19))
        self.assertEqual(Notification.objects.count(), 0)

    def test_d1_at_0820(self):
        run(at(4, 8, 20))
        n = Notification.objects.get(member=self.m2)
        self.assertEqual(n.kind, Notification.Kind.D1)
        self.assertEqual(n.space, self.a)
        self.assertEqual(n.text, "학예회 참가 인원 — 내일 17:00 마감")
        self.assertEqual(n.link, f"/c/{self.c.link_token}/u/{self.r2.key}")
        self.assertFalse(n.read)

    def test_d0_at_0820_same_day(self):
        run(at(4, 8, 20))
        run(at(5, 8, 20))
        self.assertEqual(self.kinds(self.m2), ["d0", "d1"])
        self.assertEqual(Notification.objects.get(member=self.m2, kind="d0").text, "학예회 참가 인원 — 오늘 17:00 마감")

    def test_two_days_before_nothing(self):
        run(at(3, 12, 0))
        self.assertEqual(Notification.objects.count(), 0)

    def test_repeated_runs_create_one(self):
        for mm in (20, 25, 30):
            run(at(4, 8, mm))
        run(at(4, 23, 55))
        self.assertEqual(Notification.objects.filter(member=self.m2).count(), 1)

    def test_late_run_same_day_still_sends(self):
        run(at(5, 15, 40))  # 서버가 오후에 다시 켜짐
        self.assertEqual(self.kinds(self.m2), ["d0"])

    def test_after_due_nothing(self):
        run(at(5, 17, 5))
        self.assertEqual(Notification.objects.count(), 0)

    def test_submitted_excluded(self):
        save_cell(self.r1, self.f, "21", "")
        run(at(4, 8, 20))
        self.assertEqual(self.kinds(self.owner), [])
        self.assertEqual(self.kinds(self.m2), ["d1"])

    def test_locked_excluded(self):
        Collection.objects.filter(pk=self.c.pk).update(closed_at=at(4, 8, 0))
        run(at(4, 8, 20))
        self.assertFalse(Notification.objects.filter(space=self.a).exists())

    def test_no_due_excluded(self):
        Collection.objects.filter(pk=self.c.pk).update(due=None)
        run(at(4, 8, 20))
        self.assertFalse(Notification.objects.filter(space=self.a).exists())

    def test_spaces_isolated(self):
        run(at(4, 8, 20))
        self.assertEqual(self.kinds(self.mb), ["d1"])
        self.assertEqual(Notification.objects.get(member=self.mb).space, self.b)
        self.assertEqual(Notification.objects.filter(space=self.a).count(), 2)  # 4-1·4-2, 명부 밖 4-3은 없음

    def test_multi_group_one_notice(self):
        """여러 줄(multi): 받는 곳마다 하나. 한 줄이라도 냈으면 그 받는 곳은 낸 것."""
        c = Collection.objects.create(space=self.a, created_by=self.owner, title="준비물", multi=True,
                                      source=Collection.Source.SHEET, unit_head=["학년반", "담임"], due=at(5, 12, 0))
        f = Field.objects.create(collection=c, pos=0, name="물품", kind="text")
        Row.objects.create(collection=c, pos=0, unit="4-2", who="박서준", member=self.m2)
        r = Row.objects.create(collection=c, pos=1, unit="4-2", who="박서준", member=self.m2)
        run(at(4, 8, 20))
        self.assertEqual(Notification.objects.filter(member=self.m2, row__collection=c).count(), 1)
        save_cell(r, f, "가위", "")
        run(at(5, 8, 20))
        self.assertFalse(Notification.objects.filter(member=self.m2, row__collection=c, kind="d0").exists())

    def test_text_has_no_values(self):
        save_cell(self.r1, self.f, "987654", "")
        run(at(4, 8, 20))
        self.assertFalse(any("987654" in n.text for n in Notification.objects.all()))


class BellViewTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        run(at(4, 8, 20))
        cls.n2 = Notification.objects.get(member=cls.m2)
        cls.nb = Notification.objects.get(member=cls.mb)

    def setUp(self):
        self.client.force_login(self.u2)

    def test_shell_shows_unread_count(self):
        body = self.client.get(f"/s/{self.a.token}/").content.decode()
        self.assertIn('id="bellBtn"', body)
        self.assertIn('안 읽은 알림 1건', body)
        self.assertIn('class="dot-n"', body)

    def test_list_only_mine(self):
        r = self.client.get(f"/s/{self.a.token}/bell")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("학예회 참가 인원 — 내일 17:00 마감", body)
        self.assertIn(f"/s/{self.a.token}/bell/{self.n2.key}", body)
        own = Notification.objects.get(member=self.owner)
        self.assertNotIn(own.key, body)  # 같은 공간 다른 사람 알림
        self.assertNotIn(self.nb.key, body)
        self.assertIn("no-cache", r["Cache-Control"])

    def test_read_marks_and_redirects(self):
        r = self.client.post(f"/s/{self.a.token}/bell/{self.n2.key}")
        self.assertRedirects(r, self.n2.link, fetch_redirect_response=False)
        self.n2.refresh_from_db()
        self.assertTrue(self.n2.read)
        body = self.client.get(f"/s/{self.a.token}/").content.decode()
        self.assertIn('안 읽은 알림 0건', body)
        self.assertNotIn('class="dot-n"', body)

    def test_read_requires_post(self):
        self.assertEqual(self.client.get(f"/s/{self.a.token}/bell/{self.n2.key}").status_code, 405)

    def test_read_requires_csrf(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u2)
        self.assertEqual(c.post(f"/s/{self.a.token}/bell/{self.n2.key}").status_code, 403)
        self.n2.refresh_from_db()
        self.assertFalse(self.n2.read)

    def test_other_space_404(self):
        self.assertEqual(self.client.post(f"/s/{self.a.token}/bell/{self.nb.key}").status_code, 404)
        self.assertEqual(self.client.get(f"/s/{self.b.token}/bell").status_code, 404)  # 회원 아님

    def test_other_member_404(self):
        own = Notification.objects.get(member=self.owner)
        self.assertEqual(self.client.post(f"/s/{self.a.token}/bell/{own.key}").status_code, 404)
        own.refresh_from_db()
        self.assertFalse(own.read)

    def test_anonymous_to_login(self):
        r = Client().get(f"/s/{self.a.token}/bell")
        self.assertEqual(r.status_code, 302)


class RemindEdgeQATests(Base):
    """QA(D3) 경계: 시각(08:19/08:20·자정·23:59/00:00 마감·UTC 시계), 대상(연수 부분 저장·대리·손님), 마감 바꾸기."""

    def kinds(self, member, c=None):
        qs = Notification.objects.filter(member=member, row__collection=c or self.c)
        return sorted(qs.values_list("kind", "day"))

    def due(self, dt):
        Collection.objects.filter(pk=self.c.pk).update(due=dt)

    def test_0819_then_0820_d0(self):
        run(at(5, 8, 19))
        self.assertEqual(Notification.objects.count(), 0)
        run(at(5, 8, 20))
        self.assertEqual([k for k, _ in self.kinds(self.m2)], ["d0"])

    def test_utc_clock(self):
        """서버 시계(UTC)로 들어와도 서울 시각으로 판정: 11/3 23:20 UTC = 11/4 08:20 KST."""
        utc = ZoneInfo("UTC")
        run(datetime(2026, 11, 3, 23, 19, tzinfo=utc))
        self.assertEqual(Notification.objects.count(), 0)
        run(datetime(2026, 11, 3, 23, 20, tzinfo=utc))
        self.assertEqual(self.kinds(self.m2), [("d1", at(4, 0, 0).date())])
        # 마감 당일 00:30 KST(= 전날 15:30 UTC): 08:20 전이라 아무것도 없음
        run(datetime(2026, 11, 4, 15, 30, tzinfo=utc))
        self.assertEqual(len(self.kinds(self.m2)), 1)

    def test_around_midnight_no_early_d0(self):
        run(at(4, 23, 55))
        run(at(5, 0, 0))
        run(at(5, 0, 5))
        self.assertEqual([k for k, _ in self.kinds(self.m2)], ["d1"])

    def test_due_2359(self):
        self.due(at(5, 23, 59))
        run(at(4, 8, 20))
        run(at(5, 8, 20))
        self.assertEqual([k for k, _ in self.kinds(self.m2)], ["d0", "d1"])
        self.assertEqual(Notification.objects.get(member=self.m2, kind="d0").text, "학예회 참가 인원 — 오늘 23:59 마감")

    def test_due_0000(self):
        """마감 11/5 00:00 = 11/4 밤. D-1은 11/4 08:20('내일 00:00'), 당일 08:20은 이미 지나 없음."""
        self.due(at(5, 0, 0))
        run(at(3, 8, 20))
        self.assertEqual(Notification.objects.count(), 0)
        run(at(4, 8, 20))
        self.assertEqual(Notification.objects.get(member=self.m2).text, "학예회 참가 인원 — 내일 00:00 마감")
        run(at(5, 8, 20))
        self.assertEqual([k for k, _ in self.kinds(self.m2)], ["d1"])

    def test_due_moved_later_resends(self):
        run(at(4, 8, 20))
        self.due(at(7, 17, 0))
        run(at(5, 8, 20))  # 새 마감 기준 D-2: 없음
        self.assertEqual(len(self.kinds(self.m2)), 1)
        run(at(6, 8, 20))
        run(at(7, 8, 20))
        self.assertEqual(self.kinds(self.m2), [("d0", at(7, 0, 0).date()), ("d1", at(4, 0, 0).date()),
                                               ("d1", at(6, 0, 0).date())])
        self.assertEqual(Notification.objects.filter(member=self.m2, day=at(6, 0, 0).date()).get().text,
                         "학예회 참가 인원 — 내일 17:00 마감")

    def test_due_moved_to_today_after_d1(self):
        run(at(4, 8, 20))
        self.due(at(4, 18, 0))
        run(at(4, 9, 0))
        self.assertEqual([k for k, _ in self.kinds(self.m2)], ["d0", "d1"])
        self.assertEqual(Notification.objects.get(member=self.m2, kind="d0").text, "학예회 참가 인원 — 오늘 18:00 마감")

    def test_proxy_submitted_row_excluded(self):
        r = Client().post(f"/c/{self.c.link_token}/u/{self.r2.key}", {"f0": "12", "by": "김가람"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(self.r2.cells.values_list("by", flat=True)), {"김가람"})  # 대신 냄
        run(at(4, 8, 20))
        self.assertEqual(self.kinds(self.m2), [])
        self.assertEqual(len(self.kinds(self.owner)), 1)

    def test_guest_member_included(self):
        from accounts.models import new_guest

        g = Member.objects.create(space=self.a, name="최손님", cls="4-3", user=new_guest("최손님"))
        Row.objects.filter(pk=self.r3.pk).update(member=g)
        run(at(4, 8, 20))
        self.assertEqual(len(self.kinds(g)), 1)

    def test_member_of_other_space_on_row_excluded(self):
        """줄에 다른 공간 회원이 잘못 붙어 있어도 그 사람 🔔에는 안 간다."""
        Row.objects.filter(pk=self.r3.pk).update(member=self.mb)
        run(at(4, 8, 20))
        self.assertFalse(Notification.objects.filter(member=self.mb, row=self.r3).exists())

    def test_training_partial_save_is_target(self):
        t = Collection.objects.create(space=self.a, created_by=self.owner, title="연수", source=Collection.Source.PRESET,
                                      unit_head=["직위", "성명"], due=at(5, 17, 0))
        tf = [Field.objects.create(collection=t, pos=i, name=n, kind="text") for i, n in enumerate(["청렴", "안전"])]
        tr = Row.objects.create(collection=t, pos=0, unit="교사", who="박서준", member=self.m2)
        tr1 = Row.objects.create(collection=t, pos=1, unit="교사", who="김가람", member=self.owner)
        save_cell(tr, tf[0], "A-1", "")  # 부분 저장
        for f in tf:
            save_cell(tr1, f, "B-1", "")  # 다 채움
        run(at(4, 8, 20))
        self.assertEqual(len(self.kinds(self.m2, t)), 1)
        self.assertEqual(self.kinds(self.owner, t), [])


class BellQATests(Base):
    """QA(D3) 🔔: 링크 토큰 끼우기, 읽음 후 이동 화면, 캐시, 공간 밖 화면에서 컨텍스트 프로세서."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        run(at(4, 8, 20))
        cls.n2 = Notification.objects.get(member=cls.m2)
        cls.nb = Notification.objects.get(member=cls.mb)

    def setUp(self):
        self.client.force_login(self.u2)

    def test_own_key_under_other_space_token_404(self):
        self.client.force_login(self.ub)
        self.assertEqual(self.client.post(f"/s/{self.b.token}/bell/{self.n2.key}").status_code, 404)
        self.assertEqual(self.client.post(f"/s/{self.a.token}/bell/{self.nb.key}").status_code, 404)
        self.n2.refresh_from_db()
        self.assertFalse(self.n2.read)

    def test_bad_key_404(self):
        self.assertEqual(self.client.post(f"/s/{self.a.token}/bell/x{self.n2.key}").status_code, 404)

    def test_read_redirect_target_opens(self):
        r = self.client.post(f"/s/{self.a.token}/bell/{self.n2.key}", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "학예회 참가 인원")
        self.assertIn("no-cache", self.client.post(f"/s/{self.a.token}/bell/{self.n2.key}")["Cache-Control"])

    def test_read_twice_ok(self):
        for _ in range(2):
            self.assertEqual(self.client.post(f"/s/{self.a.token}/bell/{self.n2.key}").status_code, 302)

    def test_context_processor_outside_space_no_query(self):
        boom = mock.Mock(side_effect=AssertionError("공간 밖에서 알림을 셌다"))
        with mock.patch("collect.bell._mine", boom):
            for url in ("/privacy", "/terms", f"/c/{self.c.link_token}", f"/c/{self.c.link_token}/u/{self.r2.key}"):
                self.assertEqual(self.client.get(url).status_code, 200, url)
                self.assertEqual(Client().get(url).status_code, 200, url)
        boom.assert_not_called()

    def test_context_processor_lazy_on_partials(self):
        """공통 틀을 그리지 않는 htmx 부분 응답(live)에서는 세지 않는다."""
        boom = mock.Mock(side_effect=AssertionError("부분 응답에서 알림을 셌다"))
        with mock.patch("collect.bell._mine", boom):
            r = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/live", HTTP_HX_REQUEST="true")
        self.assertIn(r.status_code, (200, 204))
        boom.assert_not_called()

    def test_shell_one_count_query(self):
        from django.test import RequestFactory

        from .bell import unread

        req = RequestFactory().get("/")
        req.space, req.member = self.a, self.m2
        with self.assertNumQueries(0):
            ctx = unread(req)
        with self.assertNumQueries(1):
            self.assertEqual(str(ctx["bell_n"]), "1")
