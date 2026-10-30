"""D1 마감 정하기·바꾸기 + 마감 안내 문구(prototype collectDetail dueBox, 모습은 design/collect-detail.html 마감 카드).

불변식
- 마감은 만든 사람만 정하고 바꾼다(prototype c.mine). 다른 회원은 보기만, 보내면 403.
- 다른 공간이면 404. CSRF 필요. 순번 ID 없음.
- 날짜·시각은 Asia/Seoul로 읽는다. 과거 날짜도 받는다(prototype에 막는 규칙 없음).
- 안내 문구는 '[고티처] 「제목」 마감 안내/변경: …' + 받는 링크(SITE_URL).
- 바꾼 마감은 받는 사람 화면에 바로 보인다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from spaces.models import Member, School, Space

from .models import Collection, Field, Row

SEOUL = ZoneInfo("Asia/Seoul")


@override_settings(SITE_URL="https://t.goteacher.xyz")
class DueTests(TestCase):
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
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, c=None, space=None):
        c = c or self.c
        return f"/s/{(space or c.space).token}/c/{c.key}/due"

    def post(self, d="2027-11-03", t="17:00", client=None, c=None, space=None):
        return (client or self.client).post(self.url(c, space), {"date": d, "time": t}, HTTP_HX_REQUEST="true")

    def due(self):
        self.c.refresh_from_db()
        return self.c.due

    # ---- 정하기·바꾸기
    def test_set_due_in_seoul(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.due(), datetime(2027, 11, 3, 17, 0, tzinfo=SEOUL))
        body = r.content.decode()
        self.assertIn("11/3(수) 17:00", body)
        self.assertIn("마감 바꾸기", body)

    def test_change_due(self):
        self.post()
        r = self.post("2027-11-05", "12:30")
        self.assertEqual(self.due(), datetime(2027, 11, 5, 12, 30, tzinfo=SEOUL))
        self.assertIn("마감 변경", r.content.decode())

    def test_time_defaults_to_17(self):
        self.post(t="")
        self.assertEqual(self.due(), datetime(2027, 11, 3, 17, 0, tzinfo=SEOUL))

    def test_past_date_allowed(self):
        """prototype은 지난 날짜를 막지 않는다."""
        self.post("2020-01-02", "09:00")
        self.assertEqual(self.due(), datetime(2020, 1, 2, 9, 0, tzinfo=SEOUL))

    def test_bad_date_not_saved(self):
        for d in ("", "2026-13-40", "내일"):
            r = self.post(d)
            self.assertEqual(r.status_code, 200)
            self.assertIn("마감 날짜를 골라 주세요", r.content.decode())
        self.assertIsNone(self.due())
        self.post(t="25:99")
        self.assertIsNone(self.due())

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    # ---- 권한·격리
    def test_only_creator_changes(self):
        c2 = Client()
        c2.force_login(self.u2)
        self.assertEqual(self.post(client=c2).status_code, 403)
        self.assertIsNone(self.due())
        page = c2.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertNotIn('id="dueDlg"', page)
        self.assertNotIn("마감 정하기", page)
        self.assertIn("마감 미정", page)

    def test_other_space_404(self):
        self.assertEqual(self.post(c=self.other).status_code, 404)
        self.assertEqual(self.post(c=self.other, space=self.a).status_code, 404)
        c3 = Client()
        c3.force_login(self.ub)
        self.assertEqual(self.post(client=c3).status_code, 404)
        self.assertIsNone(self.due())

    def test_anonymous_cannot(self):
        self.client.logout()
        self.assertIn(self.post().status_code, (204, 302))
        self.assertIsNone(self.due())

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(self.post(client=c).status_code, 403)
        self.assertIsNone(self.due())
        page = c.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertIn("csrfmiddlewaretoken", page)

    def test_no_serial_id(self):
        body = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertIn(f"/s/{self.a.token}/c/{self.c.key}/due", body)
        self.assertNotRegex(body, rf"/c/{self.c.pk}(?![\w-])")

    # ---- 안내 문구
    def test_notice_has_due_and_link(self):
        r = self.post()
        body = r.content.decode()
        self.assertIn("[고티처] 「학예회 참가 인원」 마감 안내: 11/3 17:00", body)
        self.assertIn("아직 안 채운 분은 링크에서 우리 반 칸만 채워 주세요.", body)
        self.assertIn(f"https://t.goteacher.xyz/c/{self.c.link_token}", body)
        page = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertIn("마감 안내 문구 복사", page)
        self.assertIn("마감 안내: 11/3 17:00", page)

    def test_no_notice_before_due(self):
        page = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertIn("마감을 아직 정하지 않았어요", page)
        self.assertIn("마감 정하기", page)
        self.assertNotIn("마감 안내 문구 복사", page)

    def test_title_escaped_in_notice(self):
        self.c.title = "<b>x</b>"
        self.c.save()
        body = self.post().content.decode()
        self.assertNotIn("<b>x</b>", body)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", body)

    # ---- 반영
    def test_recv_shows_changed_due(self):
        self.post()
        self.assertIn("11/3 17:00", Client().get(f"/c/{self.c.link_token}").content.decode())
        self.post("2027-11-05", "12:30")
        recv = Client().get(f"/c/{self.c.link_token}").content.decode()
        self.assertIn("11/5 12:30", recv)
        self.assertNotIn("11/3 17:00", recv)

    def test_detail_title_updated_oob(self):
        body = self.post().content.decode()
        self.assertIn('hx-swap-oob="true"', body)
        self.assertIn('id="dueSub"', body)


# ---- QA 추가(D1 경계 사례): 시간대 경계·남은 시간·지각 반영·토큰 바꿔 끼우기·문구 변형
from datetime import timezone as dt_tz  # noqa: E402
from unittest import mock  # noqa: E402

from .models import Cell  # noqa: E402
from .presets import PRESETS  # noqa: E402


def at_seoul(*a):
    return datetime(*a, tzinfo=SEOUL)


@override_settings(SITE_URL="https://t.goteacher.xyz")
class DueQATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        DueTests.setUpTestData.__func__(cls)

    setUp, url, post, due = DueTests.setUp, DueTests.url, DueTests.post, DueTests.due

    def card(self, now):
        with mock.patch("django.utils.timezone.now", return_value=now):
            return self.client.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()

    # 시간대: 23:59·00:00은 서울 기준 그 날짜로 저장·표시
    def test_2359_and_midnight_kept_in_seoul(self):
        self.post("2027-11-03", "23:59")
        self.assertEqual(self.due(), datetime(2027, 11, 3, 14, 59, tzinfo=dt_tz.utc))
        self.assertIn("11/3 23:59", Client().get(f"/c/{self.c.link_token}").content.decode())
        self.post("2027-11-04", "00:00")
        self.assertEqual(self.due(), datetime(2027, 11, 3, 15, 0, tzinfo=dt_tz.utc))
        recv = Client().get(f"/c/{self.c.link_token}").content.decode()
        self.assertIn("11/4 00:00", recv)
        self.assertNotIn("11/3 15:00", recv)

    # '오늘' 표기는 서울 날짜로(UTC로는 전날인 아침에도)
    def test_today_label_uses_seoul_date(self):
        now = at_seoul(2026, 10, 30, 8, 0)  # UTC 10/29 23:00
        with mock.patch("django.utils.timezone.now", return_value=now):
            body = self.post("2026-10-30", "17:00").content.decode()
        self.assertIn("마감 안내: 오늘 17:00", body)
        self.assertIn("9시간 0분 남았어요", body)

    def test_next_day_midnight_not_today(self):
        now = at_seoul(2026, 10, 30, 23, 30)
        with mock.patch("django.utils.timezone.now", return_value=now):
            body = self.post("2026-10-31", "00:00").content.decode()
        self.assertIn("마감 안내: 10/31 00:00", body)
        self.assertIn("0시간 30분 남았어요", body)

    def test_left_text_days_and_passed(self):
        self.c.due = at_seoul(2026, 11, 2, 19, 0)
        self.c.save()
        self.assertIn("3일 2시간 남았어요", self.card(at_seoul(2026, 10, 30, 17, 0)))
        self.assertIn("마감 시각이 지났어요", self.card(at_seoul(2026, 11, 2, 19, 0)))
        self.assertIn("마감 시각이 지났어요", self.card(at_seoul(2026, 11, 3, 9, 0)))

    def test_past_due_still_changeable(self):
        self.post("2020-01-02", "09:00")
        body = self.post("2027-01-02", "09:00").content.decode()
        self.assertIn("마감 변경", body)
        self.assertIn("마감 바꾸기", body)

    # 바꾼 마감이 지각 판정에 바로 반영
    def test_late_follows_changed_due(self):
        f = self.c.fields.first()
        row = self.c.rows.first()
        Cell.objects.create(row=row, field=f, value="20")
        Cell.objects.filter(row=row).update(at=at_seoul(2027, 11, 3, 18, 0))
        self.post("2027-11-03", "17:00")
        self.assertIn("늦게 냈어요", Client().get(f"/c/{self.c.link_token}").content.decode())
        self.post("2027-11-03", "18:00")  # 낸 시각과 같으면 지각 아님
        recv = Client().get(f"/c/{self.c.link_token}").content.decode()
        self.assertNotIn("늦게 냈어요", recv)
        self.assertIn("냈어요", recv)

    # 토큰 바꿔 끼우기
    def test_token_swaps_404(self):
        a, k = self.a.token, self.c.key
        for u in (f"/s/{a}/c/{self.c.link_token}/due", f"/s/{a}/c/{self.other.key}/due",
                  f"/s/{self.b.token}/c/{k}/due", f"/s/{k}/c/{k}/due", f"/s/{a}x/c/{k}/due"):
            r = self.client.post(u, {"date": "2027-11-03", "time": "17:00"})
            self.assertIn(r.status_code, (403, 404), u)
        self.assertIsNone(self.due())

    def test_no_creator_nobody_changes(self):
        self.c.created_by = None
        self.c.save()
        self.assertEqual(self.post().status_code, 403)
        self.assertIsNone(self.due())
        page = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertNotIn('id="dueDlg"', page)

    # 안내 문구·알림 문구 변형(prototype: 안내/변경, 정했어요/바꿨어요, 내 줄/우리 반 칸/내 칸)
    def test_toast_text_first_and_changed(self):
        b1 = self.post().content.decode()
        self.assertIn("마감을 11/3 17:00로 정했어요. 받는 선생님 화면에 바로 반영되고, 안내 문구를 복사했어요.", b1)
        b2 = self.post("2027-11-05", "12:30").content.decode()
        self.assertIn("마감을 11/5 12:30로 바꿨어요.", b2)
        page = self.client.get(f"/s/{self.a.token}/c/{self.c.key}/").content.decode()
        self.assertNotIn("data-autocopy", page)  # 다시 열 때는 저절로 복사하지 않음
        self.assertIn("마감 안내: 11/5 12:30", page)  # 처음 연 화면은 '안내'

    def test_notice_where_variants(self):
        self.c.unit_head = ["이름"]
        self.c.save()
        self.assertIn("링크에서 내 칸만", self.post().content.decode())
        self.c.source, self.c.unit_head = Collection.Source.PRESET, PRESETS["training"]["unit_head"]
        self.c.save()
        self.assertIn("링크에서 내 줄만", self.post().content.decode())

    def test_oob_sub_has_new_due(self):
        body = self.post("2027-11-05", "12:30").content.decode()
        sub = body[body.index('id="dueSub"'):]
        self.assertIn("11/5(금) 12:30", sub)
