"""G1 간이 '오늘' — 모습은 design/today.html, 동작은 prototype/assets/app.js today()·한 줄 입력.

불변식
- 공간 격리: 일정·낼 것·걷는 것은 request.space 안의 것만. 다른 공간 주소·다른 공간 일정 key는 404.
- 쓰기(한 줄 입력·고치기·지우기)는 POST + CSRF. GET은 405. 캐시 금지(공용 PC).
- 일정 제목·장소는 이스케이프(XSS). 한 줄은 서버가 파싱한다(브라우저 결과를 믿지 않음).
- NEIS가 비거나 실패하면 빈 칸을 정직하게("오늘 급식 정보가 없어요"). NEIS는 테스트에서 목.
- 첫 화면 핵심: 가장 급한 것(제목 줄), 지금 블록, 바뀐 것(누가 무엇을).
- 바뀐 것: 이미 정한 날짜·블록·장소를 바꾸면 changed_from과 바꾼 사람을 남긴다. 시간 미정에 처음 블록을 정하는 건 알리지 않는다.
"""
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from collect.models import Collection, Field, Row
from collect.submit import save_cell
from spaces.models import Member, School, Space

from .models import Event

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 30, 11, 5, tzinfo=KST)  # 금요일 2블록
TODAY = NOW.date()
MEAL = [{"ymd": "20261030", "kcal": 642, "count": 500, "dishes": [
    {"name": "잡곡밥", "allergy": [], "self": False},
    {"name": "쇠고기미역국", "allergy": [5, 6, 16], "self": False},
    {"name": "돈육장조림", "allergy": [1, 5, 6, 10], "self": False},
]}]
SCHED = [{"ymd": "20261030", "name": "독서주간"}]


@override_settings(SITE_URL="https://t.goteacher.xyz")
class TodayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="J10", neis_code="7530000", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.me = Member.objects.create(space=cls.a, name="정하은", cls="4-2", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="김도윤", cls="4-3", user=cls.u2)
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        self.client.force_login(self.ua)
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.meals", return_value=MEAL),
             mock.patch("spaces.neis.schedule", return_value=SCHED)]
        self.now, self.meals, self.sched = (x.start() for x in p)
        for x in p:
            self.addCleanup(x.stop)

    def url(self, tail="", space=None):
        return f"/s/{(space or self.a).token}/today{tail}"

    def ev(self, space=None, by=None, **kw):
        kw.setdefault("date", TODAY)
        kw.setdefault("title", "학년협의")
        return Event.objects.create(space=space or self.a, by=by or self.me, **kw)

    def add(self, line, client=None, hx=True):
        headers = {"HX-Request": "true"} if hx else {}
        return (client or self.client).post(self.url("/events"), {"line": line}, headers=headers)

    def collection(self, by, title, due=None, space=None):
        space = space or self.a
        c = Collection.objects.create(space=space, created_by=by, title=title, source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"], due=due)
        f = Field.objects.create(collection=c, pos=0, name="참가(명)", kind="number")
        rows = [Row.objects.create(collection=c, pos=i, unit=m.cls, who=m.name, member=m)
                for i, m in enumerate(Member.objects.in_space(space).order_by("pk"))]
        return c, f, rows

    # ---------- 접근·격리 ----------
    def test_anonymous_goes_to_login(self):
        r = Client().get(self.url())
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/start"))

    def test_other_space_is_404(self):
        self.assertEqual(self.client.get(self.url(space=self.b)).status_code, 404)
        self.assertEqual(self.add("오늘 회의", client=None).status_code, 200)
        r = self.client.post(self.url("/events", space=self.b), {"line": "오늘 몰래"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Event.objects.filter(title__contains="몰래").exists())

    def test_other_space_event_key_is_404(self):
        theirs = self.ev(space=self.b, by=self.mb, title="옆 학년 비밀", place="강당")
        for tail in (f"/e/{theirs.key}", f"/e/{theirs.key}/delete"):
            r = self.client.post(self.url(tail), {"date": "2026-10-30", "slot": "b1", "place": "운동장"})
            self.assertEqual(r.status_code, 404, tail)
        theirs.refresh_from_db()
        self.assertEqual(theirs.place, "강당")

    def test_other_space_events_never_shown(self):
        self.ev(space=self.b, by=self.mb, title="옆 학년 비밀 일정", slot="b2", place="과학실")
        r = self.client.get(self.url())
        self.assertNotContains(r, "옆 학년 비밀 일정")
        # 장소 충돌도 내 공간 일정끼리만
        self.assertNotContains(self.client.post(self.url("/parse"), {"line": "오늘 2블록 과학실 실험"}), "겹쳐요")

    def test_writes_need_post(self):
        e = self.ev()
        for tail in ("/events", "/parse", f"/e/{e.key}", f"/e/{e.key}/delete"):
            self.assertEqual(self.client.get(self.url(tail)).status_code, 405, tail)

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        e = self.ev()
        self.assertEqual(c.post(self.url("/events"), {"line": "오늘 회의"}).status_code, 403)
        self.assertEqual(c.post(self.url(f"/e/{e.key}/delete")).status_code, 403)
        self.assertTrue(Event.objects.filter(pk=e.pk).exists())

    def test_no_cache(self):
        for r in (self.client.get(self.url()), self.add("오늘 회의"),
                  self.client.post(self.url("/parse"), {"line": "오늘 회의"})):
            self.assertIn("no-store", r["Cache-Control"])

    def test_root_goes_to_today(self):
        self.assertEqual(self.client.get("/")["Location"], self.url())

    def test_shell_has_today_first(self):
        html = self.client.get(self.url()).content.decode()
        nav = html[html.index('class="nav"'):]
        self.assertLess(nav.index(f'href="{self.url()}"'), nav.index(f'href="/s/{self.a.token}/c/"'))
        self.assertIn(f'href="{self.url()}" aria-current="page"', html)

    # ---------- NEIS ----------
    def test_meal_and_schedule(self):
        r = self.client.get(self.url())
        self.assertContains(r, "쇠고기미역국")
        self.assertContains(r, "642kcal")
        self.assertContains(r, "독서주간")
        self.assertContains(r, "난류")  # 알레르기 1번
        self.assertContains(r, "쇠고기")  # 16번
        self.meals.assert_called_with("J10", "7530000", "20261030", "20261030")

    def test_neis_empty_is_honest(self):
        self.meals.return_value = []
        self.sched.return_value = []
        r = self.client.get(self.url())
        self.assertContains(r, "오늘 급식 정보가 없어요")
        self.assertContains(r, "오늘 학사일정이 없어요")

    # ---------- 첫 화면 핵심 ----------
    def test_now_block(self):
        r = self.client.get(self.url())
        self.assertContains(r, "지금 <b>2블록 10:30~12:20</b>", html=False)
        self.now.return_value = NOW.replace(hour=7)
        self.assertContains(self.client.get(self.url()), "1블록 08:40 시작 전")

    def test_most_urgent_todo_in_title(self):
        due = NOW.replace(hour=14, minute=0)
        c, _, rows = self.collection(self.m2, "학예회 반별 참가 인원", due=due)
        self.collection(self.m2, "방과후 수요조사", due=due + timedelta(days=3))
        r = self.client.get(self.url())
        html = r.content.decode()
        h1 = html[html.index("<h1"):html.index("</h1>")]
        self.assertIn("학예회 반별 참가 인원", h1)
        self.assertIn("오늘 14:00", h1)
        mine = next(x for x in rows if x.member_id == self.me.pk)
        self.assertContains(r, f'href="/c/{c.link_token}/u/{mine.key}"')
        self.assertContains(r, "방과후 수요조사")

    def test_no_todo(self):
        r = self.client.get(self.url())
        self.assertContains(r, "오늘 낼 것은 모두 끝났어요")
        self.assertContains(r, "오늘 마감 없음")

    def test_sent_progress(self):
        c, f, rows = self.collection(self.me, "체험학습 동의서 수합")
        save_cell(rows[1], f, "3", self.m2)
        self.collection(self.mb, "옆 학년 걷기", space=self.b)
        r = self.client.get(self.url())
        self.assertContains(r, "체험학습 동의서 수합")
        self.assertContains(r, f'href="/s/{self.a.token}/c/{c.key}/"')
        self.assertContains(r, "1/2반")
        self.assertNotContains(r, "옆 학년 걷기")

    # ---------- 한 줄 입력 ----------
    def test_add_today(self):
        r = self.add("오늘 2블록 4학년 과학실 안전교육 @김민준")
        self.assertEqual(r.status_code, 200)
        e = Event.objects.get()
        self.assertEqual((e.space, e.date, e.end, e.slot, e.slot_end, e.place, e.by),
                         (self.a, TODAY, None, "b2", "b2", "과학실", self.me))
        self.assertEqual(e.title, "2블록 4학년 과학실 안전교육")
        self.assertEqual(e.target, "4학년 · @김민준")
        self.assertIsNone(e.changed_from)
        self.assertContains(r, "2블록 4학년 과학실 안전교육")
        self.assertContains(r, 'id="live"')

    def test_add_without_date_is_today_untimed(self):
        self.add("학예회 소품 준비물 확인")
        e = Event.objects.get()
        self.assertEqual((e.date, e.slot), (TODAY, ""))
        self.assertContains(self.client.get(self.url()), "학예회 소품 준비물 확인")

    def test_add_other_day_not_in_today_list(self):
        r = self.add("모레 3블록 체육관 학예회 리허설")
        e = Event.objects.get()
        self.assertEqual(e.date, TODAY + timedelta(days=2))
        self.assertContains(r, "11/1(일) 3블록")  # 저장 결과 알림
        html = self.client.get(self.url()).content.decode()
        grid = html[html.index('id="dgrid"'):html.index("<!-- /dgrid -->")]
        self.assertNotIn("학예회 리허설", grid)

    def test_add_range_shows_on_middle_day(self):
        self.add("10/28~11/2 독서주간")
        r = self.client.get(self.url())
        self.assertContains(r, "독서주간")
        self.assertContains(r, "10/28(수)~11/2(월)")

    def test_add_without_js_redirects(self):
        r = self.add("오늘 회의", hx=False)
        self.assertEqual((r.status_code, r["Location"]), (303, self.url()))

    def test_add_rejects_empty_and_long(self):
        for line in ("", "   ", "오늘", "가" * 201):
            r = self.add(line)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["HX-Retarget"], "#qpop")
        self.assertFalse(Event.objects.exists())

    def test_xss_title_and_place(self):
        self.add('오늘 <img src=x onerror=alert(1)> "회의"')
        e = self.ev(title="<script>alert(1)</script>", place="<b>강당</b>", slot="b1")
        e.changed_from = [["장소", "<i>x</i>", "<b>강당</b>"]]
        e.save()
        for r in (self.client.get(self.url()), self.client.post(self.url("/parse"), {"line": "<script>x</script> 강당"})):
            html = r.content.decode()
            self.assertNotIn("<script>alert", html)
            self.assertNotIn("<img src=x", html)
            self.assertNotIn("<b>강당</b>", html)
            self.assertNotIn("<script>x", html)

    def test_preview_chips_and_conflict(self):
        self.ev(by=self.m2, title="과학 실험 수업", slot="b2", slot_end="b3", place="과학실")
        r = self.client.post(self.url("/parse"), {"line": "오늘 2블록 과학실 안전교육"})
        self.assertContains(r, "2블록")
        self.assertContains(r, "과학실")
        self.assertContains(r, "과학실이 겹쳐요")
        self.assertContains(r, "과학 실험 수업")
        self.assertContains(r, "김도윤")
        # 다른 블록·다른 날은 겹치지 않음
        self.assertNotContains(self.client.post(self.url("/parse"), {"line": "오늘 1블록 과학실 실험"}), "겹쳐요")
        self.assertNotContains(self.client.post(self.url("/parse"), {"line": "내일 2블록 과학실 실험"}), "겹쳐요")
        # 저장은 되고 알림에 겹침을 알린다(prototype도 경고만)
        r = self.add("오늘 3블록 과학실 정리")
        self.assertContains(r, "겹쳐요")
        self.assertEqual(Event.objects.filter(place="과학실").count(), 2)

    def test_preview_empty(self):
        r = self.client.post(self.url("/parse"), {"line": "  "})
        self.assertContains(r, 'id="qpop" hidden')  # 빈 칸이면 미리보기를 닫는다(자리는 남김)
        self.assertNotContains(r, "chips")

    # ---------- 고치기·바뀐 것 ----------
    def test_edit_records_change_and_who(self):
        e = self.ev(title="4학년 학년협의", place="4-3 교실", slot="pm", slot_end="pm")
        c = Client()
        c.force_login(self.u2)
        r = c.post(self.url(f"/e/{e.key}"), {"date": "2026-10-30", "slot": "pm", "place": "4-1 교실"},
                   headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.place, "4-1 교실")
        self.assertEqual(e.by, self.m2)
        self.assertEqual(e.changed_from, [["장소", "4-3 교실", "4-1 교실"]])
        html = self.client.get(self.url()).content.decode()
        feed = html[html.index('id="feed"'):]
        self.assertIn("김도윤", feed)
        self.assertIn("<del>4-3 교실</del>", feed)
        self.assertIn("4-1 교실", feed)

    def test_feed_josa_after_numbers(self):
        """리드 결정: 숫자 뒤 조사는 읽는 소리로('13:10으로', '15:30을')."""
        self.ev(title="학년부장 협의회 15:30", slot="pm", slot_end="pm")
        self.ev(title="수영교육", slot="b3", slot_end="b3", changed_from=[["출발", "13:00", "13:10"]])
        feed = self.client.get(self.url()).content.decode()
        self.assertIn("협의회 15:30을 올렸어요", feed)
        self.assertIn("<b>13:10</b>으로 바꿨어요", feed)

    def test_edit_slot_and_date_change(self):
        e = self.ev(slot="b1", slot_end="b2")
        self.client.post(self.url(f"/e/{e.key}"), {"date": "2026-11-02", "slot": "b3", "place": ""})
        e.refresh_from_db()
        self.assertEqual((e.date, e.slot, e.slot_end), (TODAY + timedelta(days=3), "b3", "b3"))
        self.assertEqual(e.changed_from, [["날짜", "10/30(금)", "11/2(월)"], ["시간", "1~2블록", "3블록"]])

    def test_first_block_for_untimed_is_not_a_change(self):
        e = self.ev(slot="")
        self.client.post(self.url(f"/e/{e.key}"), {"date": "2026-10-30", "slot": "b2", "place": ""})
        e.refresh_from_db()
        self.assertEqual(e.slot, "b2")
        self.assertIsNone(e.changed_from)

    def test_edit_rejects_bad_values(self):
        e = self.ev(slot="b1", slot_end="b1")
        for data in ({"date": "x", "slot": "b1"}, {"date": "2026-10-30", "slot": "b9"},
                     {"date": "2026-10-30", "slot": "b1", "place": "가" * 41}):
            r = self.client.post(self.url(f"/e/{e.key}"), data, headers={"HX-Request": "true"})
            self.assertEqual(r.status_code, 400, data)
        e.refresh_from_db()
        self.assertEqual((e.slot, e.date), ("b1", TODAY))

    def test_delete(self):
        e = self.ev(title="잘못 올린 일정")
        r = self.client.post(self.url(f"/e/{e.key}/delete"), headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Event.objects.filter(pk=e.pk).exists())
        self.assertNotContains(r, "잘못 올린 일정</")

    def test_feed_only_recent_and_in_space(self):
        self.add("오늘 1블록 강당 조회")
        old = self.ev(title="지난주 행사", date=TODAY - timedelta(days=7))
        Event.objects.filter(pk=old.pk).update(updated_at=NOW - timedelta(days=7))
        self.ev(space=self.b, by=self.mb, title="옆 학년 변경")
        html = self.client.get(self.url()).content.decode()
        feed = html[html.index('id="feed"'):]
        self.assertIn("강당 조회", feed)
        self.assertIn("올렸어요", feed)
        self.assertNotIn("지난주 행사", feed)
        self.assertNotIn("옆 학년 변경", feed)

    def test_now_block_marked_in_grid(self):
        self.ev(title="수업공개", slot="b2", slot_end="b2", place="4-2 교실")
        html = self.client.get(self.url()).content.decode()
        self.assertIn("2블록 · 지금", html)
        self.assertIn("수업공개", html)

    def test_timezone_is_seoul(self):
        self.assertEqual(timezone.localdate(), TODAY)
