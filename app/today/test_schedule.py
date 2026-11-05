"""R1 일정 화면(주간 보기) — 모습은 design/schedule.html, 동작은 prototype/assets/app.js cal()(주간)·openEvent.
일간·월간과 칸 안 추가는 test_schedule_modes.

불변식
- 공간 격리: 일정·취합 마감은 request.space 안의 것만. 다른 공간 주소·다른 공간 일정 key는 404.
- 쓰기(칸 안 추가·고치기·지우기)는 G1 뷰를 그대로 쓰고(view=week면 일정 판을 돌려줌) POST + CSRF. 캐시 금지.
- 일정 제목·장소는 이스케이프(XSS). 인라인 스크립트 없음(CSP).
- NEIS 학사일정이 비거나 실패하면 정직하게 알린다. NEIS는 테스트에서 목.
- 빼는 것: 블록 끌기(R3).
"""
import re
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from collect.models import Collection, Field, Row
from spaces.models import Member, School, Space

from .models import Event

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 30, 11, 5, tzinfo=KST)  # 금요일
MON = date(2026, 10, 26)
SCHED = [{"ymd": "20261027", "name": "독서주간 골든벨"}]


@override_settings(SITE_URL="https://t.goteacher.xyz")
class ScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="J10", neis_code="7530000", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:s{n}") for n in "a2b")
        cls.me = Member.objects.create(space=cls.a, name="정하은", cls="4-2", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="김도윤", cls="4-3", user=cls.u2)
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        self.client.force_login(self.ua)
        p = [mock.patch("django.utils.timezone.now", return_value=NOW),
             mock.patch("spaces.neis.meals", return_value=[]),
             mock.patch("spaces.neis.schedule", return_value=SCHED)]
        self.now, _, self.sched = (x.start() for x in p)
        for x in p:
            self.addCleanup(x.stop)

    def url(self, tail="", space=None):
        return f"/s/{(space or self.a).token}/schedule{tail}"

    def turl(self, tail, space=None):
        return f"/s/{(space or self.a).token}/today{tail}"

    def ev(self, space=None, by=None, **kw):
        kw.setdefault("date", MON + timedelta(days=1))
        kw.setdefault("title", "학년협의")
        return Event.objects.create(space=space or self.a, by=by or self.me, **kw)

    def post(self, tail, data, client=None):
        return (client or self.client).post(self.turl(tail), {"view": "week", "w": MON.isoformat(), **data},
                                            headers={"HX-Request": "true"})

    def collection(self, by, title, due, space=None):
        space = space or self.a
        c = Collection.objects.create(space=space, created_by=by, title=title, source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"], due=due)
        Field.objects.create(collection=c, pos=0, name="참가(명)", kind="number")
        rows = [Row.objects.create(collection=c, pos=i, unit=m.cls, who=m.name, member=m)
                for i, m in enumerate(Member.objects.in_space(space).order_by("pk"))]
        return c, rows

    # ---------- 접근·격리 ----------
    def test_anonymous_goes_to_login(self):
        r = Client().get(self.url())
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/start"))

    def test_other_space_is_404(self):
        self.assertEqual(self.client.get(self.url(space=self.b)).status_code, 404)
        r = self.client.post(self.turl("/events", space=self.b), {"line": "오늘 몰래", "view": "week"})
        self.assertEqual(r.status_code, 404)

    def test_other_space_event_key_is_404(self):
        theirs = self.ev(space=self.b, by=self.mb, title="옆 학년 비밀", place="강당")
        for tail in (f"/e/{theirs.key}", f"/e/{theirs.key}/delete"):
            r = self.post(tail, {"date": "2026-10-30", "slot": "b1", "place": "운동장"})
            self.assertEqual(r.status_code, 404, tail)
        theirs.refresh_from_db()
        self.assertEqual(theirs.place, "강당")

    def test_other_space_never_shown(self):
        self.ev(space=self.b, by=self.mb, title="옆 학년 비밀 일정", slot="b2", place="과학실")
        self.collection(self.mb, "옆 학년 걷기", NOW + timedelta(hours=2), space=self.b)
        r = self.client.get(self.url())
        self.assertNotContains(r, "옆 학년 비밀 일정")
        self.assertNotContains(r, "옆 학년 걷기")
        # 장소 충돌도 내 공간 일정끼리만
        r = self.post("/parse", {"line": "10/27 2블록 과학실 실험"})
        self.assertNotContains(r, "겹쳐요")

    def test_get_only_and_no_cache_and_csrf(self):
        self.assertEqual(self.client.post(self.url()).status_code, 405)
        r = self.client.get(self.url())
        self.assertIn("no-store", r["Cache-Control"])
        self.assertIn("no-store", self.post("/events", {"line": "10/27 회의"})["Cache-Control"])
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ua)
        self.assertEqual(c.post(self.turl("/events"), {"line": "오늘 회의", "view": "week"}).status_code, 403)

    def test_no_inline_script(self):
        html = self.client.get(self.url()).content.decode()
        for tag in re.findall(r"<script[^>]*>", html):
            self.assertIn("src=", tag)
        self.assertNotRegex(html, r"\son[a-z]+=")
        self.assertNotIn("hx-on", html)

    def test_shell_has_schedule_menu(self):
        html = self.client.get(self.url()).content.decode()
        self.assertIn(f'href="{self.url()}" aria-current="page"', html)
        nav = html[html.index('class="nav"'):]
        self.assertLess(nav.index(f'href="/s/{self.a.token}/c/"'), nav.index(f'href="{self.url()}"'))
        self.assertLess(nav.index(f'href="{self.url()}"'), nav.index('href="/forms"'))
        today = self.client.get(self.turl("")).content.decode()
        self.assertIn(f'href="{self.url()}"', today)

    # ---------- 주간판 ----------
    def test_week_board(self):
        mine = self.ev(title="4-2 수업공개", slot="b2", place="4-2 교실")
        self.ev(by=self.m2, title="과학실 안전교육", date=MON + timedelta(days=3), slot="b3", place="과학실")
        self.ev(title="학예회 소품 확인", date=MON)  # 시간 미정
        self.ev(title="지난주 일", date=MON - timedelta(days=3))
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("이번 주 일정", html)
        self.assertIn("10월 26일 ~ 30일", html)
        for d in ("10/26", "10/27", "10/28", "10/29", "10/30"):
            self.assertIn(d, html)
        self.assertIn("금 · 오늘", html)
        for s in ("1블록", "2블록", "3블록", "방과후", "시간 미정"):
            self.assertIn(s, html)
        self.assertIn(f'data-open="#ev-{mine.key}"', html)
        self.assertRegex(html, r'class="ev mine[^"]*" data-open="#ev-' + mine.key)
        self.assertIn("과학실 · 김도윤", html)
        self.assertIn("학예회 소품 확인", html)
        self.assertNotIn("지난주 일", html)
        self.assertIn("4학년 · 학교", html)

    def test_untimed_row_and_multi_day(self):
        e = self.ev(title="독서 기간", date=MON - timedelta(days=2), end=MON + timedelta(days=1))
        html = self.client.get(self.url()).content.decode()
        self.assertEqual(html.count(f'data-open="#ev-{e.key}"'), 2)  # 월·화
        self.assertIn('data-when="10/27(화) · 시간 미정"><button', html)

    def test_neis_in_board(self):
        html = self.client.get(self.url()).content.decode()
        self.assertRegex(html, r'class="ev neis"[^>]*>독서주간 골든벨')
        self.sched.assert_called_with("J10", "7530000", "20261026", "20261030")

    def test_neis_empty_is_honest(self):
        self.sched.return_value = []
        r = self.client.get(self.url())
        self.assertContains(r, "이 주 학사일정이 없어요.")
        self.assertNotContains(r, "불러오지 못했어요")

    def test_neis_failure_differs_from_empty(self):
        from spaces import neis
        self.sched.return_value = neis.Failed()
        r = self.client.get(self.url())
        self.assertContains(r, "나이스에서 불러오지 못했어요. 잠시 뒤 다시 볼게요.")
        self.assertNotContains(r, "이 주 학사일정이 없어요")

    def test_due_on_board_and_list(self):
        c, rows = self.collection(self.m2, "학예회 의상 대여 수량", NOW.replace(hour=12, minute=0) - timedelta(days=2))
        mc, _ = self.collection(self.me, "반별 참가 인원", NOW.replace(hour=15, minute=0))
        self.collection(self.m2, "다음 달 것", NOW + timedelta(days=30))
        r = self.client.get(self.url())
        html = r.content.decode()
        self.assertRegex(html, r'class="ev due"[^>]*><span class="k">마감</span> 학예회 의상 대여 수량 · 12:00')
        mine = next(x for x in rows if x.member_id == self.me.pk)
        self.assertIn(f'href="/c/{c.link_token}/u/{mine.key}"', html)
        self.assertIn(f'href="/s/{self.a.token}/c/{mc.key}/"', html)
        self.assertIn("내가 걷는 것", html)
        self.assertIn("내가 낼 것 · 김도윤", html)
        self.assertNotIn("다음 달 것", html)
        self.assertIn("마감 <b class=\"num\">2건</b>", html)

    def test_week_navigation(self):
        self.ev(title="다음 주 협의", date=MON + timedelta(days=8))
        r = self.client.get(self.url(), {"w": "2026-11-04"})  # 수요일을 줘도 그 주 월요일로
        self.assertContains(r, "다음 주 일정")
        self.assertContains(r, "다음 주 협의")
        self.assertContains(r, "11월 2일 ~ 6일")
        self.assertContains(r, 'name="w" value="2026-11-09"')  # 다음 주 단추
        self.assertContains(r, 'name="w" value="2026-10-26"')  # 지난주 단추
        self.sched.assert_called_with("J10", "7530000", "20261102", "20261106")
        for bad in ("x", "2026-02-30", "99999-01-01"):
            self.assertContains(self.client.get(self.url(), {"w": bad}), "이번 주 일정")

    def test_weekend_shows_next_week(self):
        self.now.return_value = NOW + timedelta(days=1)  # 토요일
        self.assertContains(self.client.get(self.url()), "11월 2일 ~ 6일")

    def test_xss_escaped(self):
        self.ev(title='<script>alert(1)</script>', place='<img src=x onerror=alert(1)>')
        html = self.client.get(self.url()).content.decode()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)

    # ---------- 일정 올리기(칸 없이 불러도 파서 결과로 놓인다) ----------
    def test_add_returns_week(self):
        r = self.post("/events", {"line": "10/29 3블록 과학실 학예회 리허설"})
        self.assertEqual(r.status_code, 200)
        e = Event.objects.get()
        self.assertEqual((e.date, e.slot, e.place, e.by), (MON + timedelta(days=3), "b3", "과학실", self.me))
        html = r.content.decode()
        self.assertIn('id="week"', html)
        self.assertNotIn('id="live"', html)
        self.assertIn("학예회 리허설", html)
        self.assertIn("data-toast-now", html)

    def test_add_keeps_shown_week(self):
        r = self.client.post(self.turl("/events"), {"view": "week", "w": "2026-11-02", "line": "11/3 강당 발표회"},
                             headers={"HX-Request": "true"})
        self.assertContains(r, "발표회")
        self.assertContains(r, "11월 2일 ~ 6일")

    def test_add_without_htmx_redirects_to_schedule(self):
        r = self.client.post(self.turl("/events"), {"view": "week", "w": MON.isoformat(), "line": "10/27 회의"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r["Location"], self.url() + "?w=2026-10-26")

    # ---------- 고치기·지우기 ----------
    def test_edit_marks_changed(self):
        e = self.ev(by=self.m2, title="학예회 리허설", slot="b2", place="강당")
        r = self.post(f"/e/{e.key}", {"date": "2026-10-29", "slot": "b3", "place": "체육관"})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.by, self.me)
        self.assertTrue(e.changed_from)
        html = r.content.decode()
        self.assertIn('id="week"', html)
        self.assertIn("이번 주 바뀐 것", html)
        self.assertIn("<del>강당</del>", html)
        self.assertIn("체육관", html)
        self.assertIn("정하은", html)

    def test_changed_list_from_week_only(self):
        self.ev(title="이번 주 바뀐 일", changed_from=[["장소", "강당", "체육관"]])
        self.ev(title="다음 주 바뀐 일", date=MON + timedelta(days=9), changed_from=[["장소", "강당", "체육관"]])
        html = self.client.get(self.url()).content.decode()
        lst = html[html.index('id="chg"'):]
        self.assertIn("이번 주 바뀐 일", lst)
        self.assertNotIn("다음 주 바뀐 일", lst)

    def test_dialog_shows_conflict(self):
        self.ev(by=self.m2, title="안전교육", slot="b2", place="과학실")
        mine = self.ev(title="실험 수업", slot="b2", place="과학실")
        html = self.client.get(self.url()).content.decode()
        dlg = html[html.index(f'id="ev-{mine.key}"'):]
        dlg = dlg[:dlg.index("</form>")]
        self.assertIn("과학실이 겹쳐요", dlg)
        self.assertIn("안전교육", dlg)
        self.assertIn('name="view" value="week"', dlg)

    def test_delete_returns_week(self):
        e = self.ev(title="지울 일정")
        r = self.post(f"/e/{e.key}/delete", {})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Event.objects.filter(pk=e.pk).exists())
        self.assertContains(r, 'id="week"')
        self.assertContains(r, "지웠어요")

    def test_today_page_responses_unchanged(self):
        e = self.ev(date=NOW.date())
        r = self.client.post(self.turl(f"/e/{e.key}/delete"), headers={"HX-Request": "true"})
        self.assertContains(r, 'id="live"')
