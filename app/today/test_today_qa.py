"""G1 QA 보강 — 구현 테스트(test_today.py)에 없던 경계만 더한다.

- 장소 충돌(TR.conflicts): 기간 일정끼리·블록 범위 경계·시간 미정 제외·비슷한 이름 장소.
- 고치기(prototype setWhen): 블록 → 시간 미정도 '바뀐 것', 기간은 '기간'으로·끝 날짜도 같이 옮김,
  바뀐 것이 없으면 쓴 사람을 바꾸지 않음, 형식은 맞지만 없는 날짜는 400.
- NEIS: 목이 아닌 실제 실패(네트워크 오류)에도 화면이 뜨고 빈 칸 문구가 나온다.
- 낼 것·걷는 것: D6 목록(collect.lists)과 같은 판정(제목 집합·건수).
- 시간대: 자정 전후의 '오늘', 블록 시작 시각 경계, 바뀐 것의 '어제부터' 경계.
- 격리: 회원 아닌 로그인 사용자, 다른 공간 회원이 key를 알아도 404. 알림(toast) 이스케이프.
"""
from datetime import datetime, timedelta
from unittest import mock
from urllib.error import URLError
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from collect.models import Collection, Field, Row
from collect.submit import save_cell
from spaces.models import Member, School, Space

from .models import Event

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 30, 11, 5, tzinfo=KST)  # 금요일 2블록
TODAY = NOW.date()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="J10", neis_code="7530000", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub, cls.uz = (User.objects.create(username=f"google:q{n}") for n in "a2bz")
        cls.me = Member.objects.create(space=cls.a, name="정하은", cls="4-2", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="김도윤", cls="4-3", user=cls.u2)
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        self.client.force_login(self.ua)
        self.now = mock.patch("django.utils.timezone.now", return_value=NOW).start()
        self.addCleanup(mock.patch.stopall)

    def neis_ok(self):
        mock.patch("spaces.neis.meals", return_value=[]).start()
        mock.patch("spaces.neis.schedule", return_value=[]).start()

    def url(self, tail="", space=None):
        return f"/s/{(space or self.a).token}/today{tail}"

    def ev(self, space=None, by=None, **kw):
        kw.setdefault("date", TODAY)
        kw.setdefault("title", "학년협의")
        return Event.objects.create(space=space or self.a, by=by or self.me, **kw)

    def preview(self, line):
        return self.client.post(self.url("/parse"), {"line": line}).content.decode()


class ConflictTests(Base):
    def setUp(self):
        super().setUp()
        self.neis_ok()

    def test_range_event_conflicts_on_middle_day(self):
        self.ev(by=self.m2, title="독서주간 부스", date=TODAY - timedelta(2), end=TODAY + timedelta(3),
                slot="b2", slot_end="b2", place="강당")
        self.assertIn("강당이 겹쳐요", self.preview("오늘 2블록 강당 리허설"))
        self.assertIn("독서주간 부스", self.preview("11/2 2블록 강당 리허설"))
        self.assertNotIn("겹쳐요", self.preview("11/3 2블록 강당 리허설"))  # 기간 다음 날

    def test_new_range_conflicts_with_single_day(self):
        self.ev(by=self.m2, title="학예회 리허설", date=TODAY + timedelta(5), slot="b3", slot_end="b3", place="체육관")
        self.assertIn("겹쳐요", self.preview("11/2~11/4 3블록 체육관 대청소"))
        self.assertNotIn("겹쳐요", self.preview("11/2~11/3 3블록 체육관 대청소"))

    def test_block_range_boundaries(self):
        self.ev(by=self.m2, title="오전 연수", slot="b1", slot_end="b2", place="시청각실")
        self.assertIn("겹쳐요", self.preview("오늘 2블록 시청각실 협의"))
        self.assertIn("겹쳐요", self.preview("오늘 2~3블록 시청각실 협의"))
        self.assertNotIn("겹쳐요", self.preview("오늘 3블록 시청각실 협의"))

    def test_untimed_and_similar_place_do_not_conflict(self):
        self.ev(by=self.m2, title="종일 행사", place="과학실")  # 시간 미정
        self.ev(by=self.m2, title="실험", slot="b2", slot_end="b2", place="과학실1")
        self.assertNotIn("겹쳐요", self.preview("오늘 2블록 과학실 정리"))
        self.assertNotIn("겹쳐요", self.preview("오늘 과학실 정리"))  # 새 일정이 시간 미정


class EditRuleTests(Base):
    def setUp(self):
        super().setUp()
        self.neis_ok()

    def post(self, e, client=None, **data):
        return (client or self.client).post(self.url(f"/e/{e.key}"), data, headers={"HX-Request": "true"})

    def test_timed_to_untimed_is_a_change(self):
        e = self.ev(slot="b1", slot_end="b1")
        self.post(e, date="2026-10-30", slot="", place="")
        e.refresh_from_db()
        self.assertEqual((e.slot, e.slot_end), ("", ""))
        self.assertEqual(e.changed_from, [["시간", "1블록", "시간 미정"]])

    def test_range_moves_end_and_says_period(self):
        e = self.ev(date=TODAY, end=TODAY + timedelta(2), title="상담주간")
        self.post(e, date="2026-11-02", slot="", place="")
        e.refresh_from_db()
        self.assertEqual((e.date, e.end), (TODAY + timedelta(3), TODAY + timedelta(5)))
        self.assertEqual(e.changed_from, [["기간", "10/30(금)~11/1(일)", "11/2(월)~11/4(수)"]])

    def test_no_change_keeps_author(self):
        e = self.ev(slot="b2", slot_end="b2", place="강당")
        c = Client()
        c.force_login(self.u2)
        r = self.post(e, client=c, date="2026-10-30", slot="b2", place="강당")
        self.assertContains(r, "바뀐 것이 없어요")
        e.refresh_from_db()
        self.assertEqual((e.by, e.changed_from), (self.me, None))

    def test_first_place_is_not_a_change(self):
        e = self.ev(slot="b2", slot_end="b2")
        c = Client()
        c.force_login(self.u2)
        self.post(e, client=c, date="2026-10-30", slot="b2", place="강당")
        e.refresh_from_db()
        self.assertEqual((e.place, e.by, e.changed_from), ("강당", self.me, None))

    def test_well_formed_but_impossible_date_is_400(self):
        e = self.ev(slot="b1", slot_end="b1")
        for bad in ("2026-02-30", "2026-13-01", "9999-12-31"):
            with self.subTest(bad=bad):
                ee = self.ev(slot="b1", slot_end="b1", end=TODAY + timedelta(1)) if bad.startswith("9999") else e
                r = self.post(ee, date=bad, slot="b1", place="")
                self.assertEqual(r.status_code, 400)


class NeisFailureTests(Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_network_error_page_still_renders(self):
        with mock.patch("spaces.neis.urlopen", side_effect=URLError("down")):
            r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "오늘 급식 정보가 없어요")
        self.assertContains(r, "오늘 학사일정이 없어요")


class ListMatchTests(Base):
    """오늘의 '내가 낼 것'·'내가 걷는 것'은 D6 목록(/c/)과 같은 판정."""

    def setUp(self):
        super().setUp()
        self.neis_ok()

    def coll(self, by, title, space=None, **kw):
        space = space or self.a
        c = Collection.objects.create(space=space, created_by=by, title=title, source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"], **kw)
        f = Field.objects.create(collection=c, pos=0, name="참가(명)", kind="number")
        rows = [Row.objects.create(collection=c, pos=i, unit=m.cls, who=m.name, member=m)
                for i, m in enumerate(Member.objects.in_space(space).order_by("pk"))]
        return c, f, rows

    def test_todo_and_sent_match_list(self):
        self.coll(self.m2, "낼것-오늘마감", due=NOW + timedelta(hours=3))
        self.coll(self.m2, "낼것-마감없음")
        c, f, rows = self.coll(self.m2, "낼것-이미냄")
        save_cell(next(r for r in rows if r.member_id == self.me.pk), f, "2", self.me)
        self.coll(self.m2, "낼것-마감됨", closed_at=NOW - timedelta(hours=1))
        self.coll(self.mb, "옆학년-낼것", space=self.b)
        self.coll(self.me, "걷기-진행")
        self.coll(self.me, "걷기-지난것", due=NOW - timedelta(days=1))

        today = self.client.get(self.url()).context
        lst = self.client.get(f"/s/{self.a.token}/c/?tab=todo").context
        self.assertEqual([x["c"].title for x in today["todo"]], [x["c"].title for x in lst["items"]][:4])
        self.assertEqual(today["n_todo"], lst["n_todo"])
        self.assertEqual({x["c"].title for x in today["todo"]}, {"낼것-오늘마감", "낼것-마감없음"})
        sent = self.client.get(f"/s/{self.a.token}/c/?tab=sent").context
        self.assertEqual([x["c"].title for x in today["sent"]], [x["c"].title for x in sent["items"]][:3])
        self.assertEqual([x["c"].title for x in today["sent"]], ["걷기-진행"])


class TimeBoundaryTests(Base):
    def setUp(self):
        super().setUp()
        self.neis_ok()

    def at(self, h, m, day=TODAY):
        self.now.return_value = datetime(day.year, day.month, day.day, h, m, tzinfo=KST)
        return self.client.get(self.url()).content.decode()

    def test_block_start_edges(self):
        self.assertIn("1블록 08:40 시작 전", self.at(8, 39))
        self.assertIn("지금 <b>1블록 08:40~10:30</b>", self.at(8, 40))
        self.assertIn("지금 <b>2블록 10:30~12:20</b>", self.at(10, 30))
        self.assertIn("지금 <b>3블록 12:20~14:40</b>", self.at(12, 20))
        self.assertIn("지금 <b>방과후 14:40~</b>", self.at(14, 40))
        self.assertIn("지금 <b>방과후 14:40~</b>", self.at(23, 59))
        self.assertIn("1블록 08:40 시작 전", self.at(0, 0))

    def test_midnight_switches_today(self):
        self.ev(title="금요일만 일정", slot="b1", slot_end="b1")
        self.assertIn("금요일만 일정", self.at(23, 59))
        html = self.at(0, 1, day=TODAY + timedelta(1))
        self.assertNotIn("금요일만 일정</", html.split('id="feed"')[0])
        self.assertIn("10월 31일 토요일", html)
        self.assertIn("주말이에요", html)

    def test_line_after_midnight_is_new_day(self):
        self.now.return_value = datetime(2026, 10, 31, 0, 0, 30, tzinfo=KST)
        self.client.post(self.url("/events"), {"line": "오늘 회의"}, headers={"HX-Request": "true"})
        self.assertEqual(Event.objects.get().date, TODAY + timedelta(1))

    def test_feed_since_yesterday_midnight(self):
        keep = self.ev(title="어제자정 수정", date=TODAY + timedelta(1))
        drop = self.ev(title="그제밤 수정", date=TODAY + timedelta(1))
        Event.objects.filter(pk=keep.pk).update(updated_at=datetime(2026, 10, 29, 0, 0, tzinfo=KST))
        Event.objects.filter(pk=drop.pk).update(updated_at=datetime(2026, 10, 28, 23, 59, tzinfo=KST))
        feed = self.client.get(self.url()).content.decode().split('id="feed"')[1]
        self.assertIn("어제자정 수정", feed)
        self.assertIn("어제 00:00", feed)
        self.assertNotIn("그제밤 수정", feed)


class IsolationExtraTests(Base):
    def setUp(self):
        super().setUp()
        self.neis_ok()

    def test_non_member_logged_in_is_404(self):
        c = Client()
        c.force_login(self.uz)
        e = self.ev()
        self.assertEqual(c.get(self.url()).status_code, 404)
        self.assertEqual(c.post(self.url("/parse"), {"line": "오늘 회의"}).status_code, 404)
        self.assertEqual(c.post(self.url(f"/e/{e.key}/delete")).status_code, 404)
        self.assertTrue(Event.objects.filter(pk=e.pk).exists())

    def test_other_member_with_key_swapped_into_own_space_is_404(self):
        mine = self.ev(title="4학년 일정", place="강당")
        c = Client()
        c.force_login(self.ub)
        for tail in (f"/e/{mine.key}", f"/e/{mine.key}/delete"):
            r = c.post(self.url(tail, space=self.b), {"date": "2026-10-30", "slot": "b1", "place": "x"})
            self.assertEqual(r.status_code, 404, tail)
        mine.refresh_from_db()
        self.assertEqual(mine.place, "강당")

    def test_user_without_space_root_is_school_search(self):
        c = Client()
        c.force_login(self.uz)
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "spaces/start_school.html")

    def test_toast_is_escaped(self):
        r = self.client.post(self.url("/events"), {"line": '오늘 "><img src=x onerror=alert(1)> 회의'},
                             headers={"HX-Request": "true"})
        html = r.content.decode()
        self.assertIn("data-toast-now=", html)
        self.assertNotIn("<img src=x", html)
