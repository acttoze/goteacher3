"""V10 운영자 고객 분석.

불변식
- 입장권 로그인은 성공·실패 분기마다 Entry 한 건(입장권 원문·난수·IP 없음). 위조 입장권은 계정 없이.
- 계정 회원이 페이지(GET·200·HTML·htmx 아님)를 열면 그날·화면 모양별 DailyUse +1. 손님·htmx·정적·운영·업체·TV·폰은 세지 않는다.
- 경로는 모양만(공간 토큰·키·쿼리 없음). 기록이 실패해도 화면은 그대로.
- /analytics는 운영자만(그 외 404), 0건이어도 200, 이름은 내지 않는다.
- 보관 기간이 지난 기록은 purge가 지운다.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.test_legacy_sso import KEY, MB_NO, REDIS_STANDIN, ticket
from feedback.models import Feedback
from hwpfiles.retention import purge
from legacy.models import LegacyMap
from spaces.models import Member, School, Space

from .models import DailyUse, Entry
from .stats import build

User = get_user_model()


def school(name, code, legacy=True):
    s = School.objects.create(neis_atpt="J10", neis_code=code, name=name)
    if legacy:
        LegacyMap.objects.create(kind="school", legacy_id=code, obj_id=s.pk)
    return Space.objects.create(school=s, kind="school", grade=0, name=name)


def seat(space, name, group="g4", username=None, **kw):
    u = User.objects.create(username=username or f"google:{name}", first_name=name, **kw)
    return Member.objects.create(space=space, user=u, name=name, cls="4-1", group=group)


@REDIS_STANDIN
@override_settings(LEGACY_SSO_KEY=KEY)
class EntryTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.space = school("새솔초", "1234567")
        self.m = seat(self.space, "김옛날", username="OldKim")
        LegacyMap.objects.create(kind="user", legacy_id=str(MB_NO), obj_id=self.m.user.pk)

    def only(self):
        self.assertEqual(Entry.objects.count(), 1)
        return Entry.objects.get()

    def test_success_records_user_and_school_space(self):
        self.client.get("/auth/legacy", {"t": ticket()})
        e = self.only()
        self.assertEqual((e.ok, e.reason, e.user, e.space), (True, "", self.m.user, self.space))

    def test_forged_ticket_records_without_user(self):
        r = self.client.get("/auth/legacy", {"t": ticket(key="other-key-0123456789abcdef0123456789")})
        self.assertRedirects(r, "/start", fetch_redirect_response=False)
        e = self.only()
        self.assertEqual((e.ok, e.reason, e.user, e.space), (False, "bad_ticket", None, None))

    def test_replayed_ticket(self):
        t = ticket()
        self.client.get("/auth/legacy", {"t": t})
        Client().get("/auth/legacy", {"t": t})
        self.assertEqual(list(Entry.objects.order_by("pk").values_list("ok", "reason")), [(True, ""), (False, "replayed")])

    def test_locked(self):
        with mock.patch("accounts.views._locked", return_value=True):
            self.client.get("/auth/legacy", {"t": ticket()})
        e = self.only()
        self.assertEqual((e.ok, e.reason, e.user), (False, "locked", self.m.user))

    def test_switch_screen_is_a_middle_step(self):
        c = Client()
        c.force_login(User.objects.create(username="google:other"))
        r = c.get("/auth/legacy", {"t": ticket()})
        self.assertEqual(r.status_code, 200)
        e = self.only()
        self.assertEqual((e.ok, e.reason, e.user), (False, "switch_shown", self.m.user))

    def test_recording_failure_keeps_login(self):
        with mock.patch("analytics.models.Entry.objects.create", side_effect=RuntimeError):
            r = self.client.get("/auth/legacy", {"t": ticket()})
        self.assertRedirects(r, f"/s/{self.space.token}/today", fetch_redirect_response=False)


class UsageTests(TestCase):
    def client_for(self, user=None):
        """미들웨어는 커밋 뒤에 센다(on_commit): 요청마다 커밋 콜백을 실행하는 클라이언트."""
        test = self

        class C(Client):
            def request(self, **req):
                with test.captureOnCommitCallbacks(execute=True):
                    return super().request(**req)
        c = C()
        if user:
            c.force_login(user)
        return c

    def setUp(self):
        for p in (mock.patch("spaces.neis.meals", return_value=[]), mock.patch("spaces.neis.schedule", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.space = school("새솔초", "1234567")
        self.m = seat(self.space, "사일")
        self.c = self.client_for(self.m.user)
        self.today = f"/s/{self.space.token}/today"

    def test_page_view_counts_once_per_day_and_route(self):
        self.assertEqual(self.c.get(self.today + "?w=2026-11-16").status_code, 200)
        self.c.get(self.today)
        u = DailyUse.objects.get()
        self.assertEqual((u.day, u.user, u.space, u.route, u.n), (timezone.localdate(), self.m.user, self.space, "/s/<공간>/today", 2))

    def test_route_has_no_token_or_key(self):
        self.c.get(f"/s/{self.space.token}/settings")
        self.c.get("/help?q=비밀")
        routes = list(DailyUse.objects.values_list("route", flat=True))
        self.assertCountEqual(routes, ["/s/<공간>/settings", "/help"])
        for r in routes:
            self.assertNotIn(self.space.token, r)
            self.assertNotIn("?", r)
        self.assertIsNone(DailyUse.objects.get(route="/help").space)

    def test_not_counted(self):
        self.c.get(self.today, headers={"HX-Request": "true"})  # htmx 조각
        self.c.post(self.today)  # 쓰기
        self.c.get(f"/s/{self.space.token}/nope")  # 404
        self.c.get("/healthz")
        guest = Member.objects.create(space=self.space, user=User.objects.create(username="guest:abc"), name="손님", cls="4-2")
        self.assertEqual(self.client_for(guest.user).get(self.today).status_code, 200)
        self.client_for().get("/help")  # 비로그인
        staff = seat(self.space, "운영", is_staff=True)
        s = self.client_for(staff.user)
        self.assertEqual(s.get("/analytics").status_code, 200)
        s.get("/ops/feedback")
        self.assertFalse(DailyUse.objects.exists())

    def test_recording_failure_keeps_page(self):
        with mock.patch("analytics.middleware.DailyUse.objects.filter", side_effect=RuntimeError):
            self.assertEqual(self.c.get(self.today).status_code, 200)
        self.assertFalse(DailyUse.objects.exists())


class PageTests(TestCase):
    def setUp(self):
        self.a = school("가람초", "A1")
        self.b = school("나래초", "B1")
        self.new = school("새학교", "N1", legacy=False)  # 이관 아님: 대상 밖
        self.a1, self.a2, self.a3 = seat(self.a, "가1", "g1"), seat(self.a, "가2", "office"), seat(self.a, "가3", "g1")
        self.b1 = seat(self.b, "나1", "admin")
        seat(self.new, "새1")
        Member.objects.create(space=self.a, user=User.objects.create(username="guest:x"), name="손님", cls="1-1", group="g1")
        self.staff = seat(self.a, "운영자", "office", is_staff=True)

    def get(self, user, **q):
        c = Client()
        if user:
            c.force_login(user)
        return c.get("/analytics", q)

    def test_staff_only(self):
        self.assertEqual(self.get(self.staff.user).status_code, 200)
        self.assertEqual(self.get(self.a1.user).status_code, 404)
        self.assertEqual(self.get(None).status_code, 404)
        lone = User.objects.create(username="google:lone", is_staff=True)  # 공간 없는 운영자도 볼 수 있다(틀만 다름)
        self.assertEqual(self.get(lone).status_code, 200)

    def test_empty_page(self):
        for d in ("1", "7", "30", "all", "x"):
            r = self.get(self.staff.user, d=d)
            self.assertEqual(r.status_code, 200)
        self.assertContains(r, "아직 이용 기록이 없어요")

    def test_numbers(self):
        today = timezone.localdate()
        y = today - timedelta(days=1)
        old = today - timedelta(days=20)
        DailyUse.objects.create(day=old, user=self.a1.user, space=self.a, route="/s/<공간>/today", n=3)
        DailyUse.objects.create(day=today, user=self.a1.user, space=self.a, route="/s/<공간>/today", n=2)
        DailyUse.objects.create(day=y, user=self.a2.user, space=self.a, route="/s/<공간>/c/", n=1)
        DailyUse.objects.create(day=today, user=self.b1.user, space=None, route="/help", n=4)
        DailyUse.objects.create(day=today, user=self.staff.user, space=self.a, route="/s/<공간>/today", n=9)  # 운영자: 빠진다
        Feedback.objects.create(space=self.a, member=self.a2, kind="bad", text="글씨가 작아요")
        Feedback.objects.create(space=self.a, member=self.a1, kind="back_slow")
        Entry.objects.create(user=self.a1.user, space=self.a, ok=True)
        Entry.objects.create(ok=False, reason="bad_ticket")
        Entry.objects.create(user=self.a1.user, ok=False, reason="switch_shown")  # 실패로 세지 않음

        s = build(7)
        tiles = {label: n for label, n, _ in s["tiles"]}
        self.assertEqual(tiles, {"대상 회원": 4, "들어와 본 사람": 3, "기간 이용자": 3, "다시 온 사람": 1, "받은 의견": 1,
                                 "예전 고티처로": 1, "입장 실패": 1})
        self.assertEqual([(label, n) for label, n, _ in s["funnel"]], [("대상 회원", 4), ("들어와 봄", 3), ("다시 옴", 1), ("의견 남김", 1)])
        a, b = s["schools"]
        self.assertEqual((a["name"], a["target"], a["entered"], a["pct"], a["active"], a["feedback"], a["back"], a["last"]),
                         ("나래초", 1, 1, 100, 1, 0, 0, today))
        self.assertEqual((b["name"], b["target"], b["entered"], b["pct"], b["feedback"], b["back"]), ("가람초", 3, 2, 67, 1, 1))
        self.assertEqual(s["trend"][-1]["n"], 2)  # 오늘: 가1·나1
        self.assertEqual(s["trend"][-1]["new"], 1)  # 나1이 오늘 처음, 가1은 20일 전 처음
        self.assertEqual({r["route"]: (r["n"], r["people"]) for r in s["routes"]}, {"/s/<공간>/today": (2, 1), "/s/<공간>/c/": (1, 1), "/help": (4, 1)})
        self.assertEqual(s["routes"][0]["name"], "도움말")
        self.assertEqual({g["label"]: (g["target"], g["active"]) for g in s["groups"]}, {"1학년": (2, 1), "교무실": (1, 1), "행정실": (1, 1)})
        self.assertEqual(build(None)["tiles"][2][1], 3)
        self.assertEqual(build(1)["tiles"][2][1], 2)

        r = self.get(self.staff.user, d="7")
        self.assertContains(r, "가람초")
        self.assertContains(r, "글씨가 작아요")
        self.assertNotContains(r, "가2")  # 이름은 내지 않는다(소속만)
        self.assertContains(r, "교무실")

    def test_org_space_members_are_counted(self):
        """이관 기관(공간 종류 org)도 대상이다: 표에 나오고, 소속은 기관 그룹 이름으로."""
        s = School.objects.create(neis_atpt="J10", neis_code="9000003", name="어느 교육지원청")
        LegacyMap.objects.create(kind="school", legacy_id="9000003", obj_id=s.pk)
        org = Space.objects.create(school=s, kind="org", grade=0, name="어느 교육지원청", org_groups=[{"k": "t1", "n": "학교혁신팀"}])
        m = seat(org, "기관분", group="t1")
        DailyUse.objects.create(day=timezone.localdate(), user=m.user, space=org, route="/s/<공간>/today", n=1)
        data = build(7)
        row = next(r for r in data["schools"] if r["name"] == "어느 교육지원청")
        self.assertEqual((row["target"], row["entered"]), (1, 1))
        self.assertIn(("학교혁신팀", 1, 1), [(g["label"], g["target"], g["active"]) for g in data["groups"]])

    def test_internal_account_is_left_out(self):
        """내부 시험 계정 moon: 숫자·의견·입장 기록에서 빠지고, 그 계정만 있는 학교는 표에 없다."""
        lab = school("대아초", "7731021")
        m = seat(lab, "시험", username="moon")
        DailyUse.objects.create(day=timezone.localdate(), user=m.user, space=lab, route="/s/<공간>/today", n=1)
        Entry.objects.create(user=m.user, ok=False, reason=Entry.Reason.LOCKED)
        Feedback.objects.create(space=lab, member=m, kind="bad", text="시험")
        Entry.objects.create(user=None, ok=False, reason=Entry.Reason.BAD_TICKET)  # 계정 모르는 실패는 그대로 센다
        data = build(7)
        tiles = {t[0]: t[1] for t in data["tiles"]}
        self.assertEqual((tiles["대상 회원"], tiles["들어와 본 사람"], tiles["받은 의견"], tiles["입장 실패"]), (4, 0, 0, 1))
        self.assertNotIn("대아초", [r["name"] for r in data["schools"]])
        self.assertEqual(data["recent"], [])

    def test_query_count_is_bounded(self):
        DailyUse.objects.create(day=timezone.localdate(), user=self.a1.user, space=self.a, route="/help", n=1)
        c = Client()
        c.force_login(self.staff.user)
        with _Bounded(self, 40):
            self.assertEqual(c.get("/analytics").status_code, 200)

    def test_command(self):
        out = StringIO()
        call_command("analytics", "--since", "30d", stdout=out)
        self.assertIn("대상 회원: 4", out.getvalue())
        self.assertIn("가람초", out.getvalue())


class _Bounded:
    def __init__(self, test, limit):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.test, self.limit, self.ctx = test, limit, CaptureQueriesContext(connection)

    def __enter__(self):
        return self.ctx.__enter__()

    def __exit__(self, *exc):
        self.ctx.__exit__(*exc)
        self.test.assertLess(len(self.ctx), self.limit)


class PurgeTests(TestCase):
    def test_old_records_go(self):
        space = school("가람초", "A1")
        m = seat(space, "가1")
        now = timezone.now()
        old_e = Entry.objects.create(user=m.user, ok=True)
        Entry.objects.filter(pk=old_e.pk).update(at=now - timedelta(days=366))
        Entry.objects.create(user=m.user, ok=True)
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=366), user=m.user, route="/help", n=1)
        DailyUse.objects.create(day=timezone.localdate(), user=m.user, route="/help", n=1)
        n = purge(now)
        self.assertEqual(n["analytics"], 2)
        self.assertEqual((Entry.objects.count(), DailyUse.objects.count()), (1, 1))

    @override_settings(ANALYTICS_KEEP_DAYS=730)
    def test_keep_days_setting(self):
        m = seat(school("가람초", "A1"), "가1")
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=400), user=m.user, route="/help", n=1)
        self.assertEqual(purge(timezone.now())["analytics"], 0)

    def test_account_delete_cascades(self):
        m = seat(school("가람초", "A1"), "가1")
        Entry.objects.create(user=m.user, ok=True)
        DailyUse.objects.create(day=timezone.localdate(), user=m.user, route="/help", n=1)
        m.user.delete()
        self.assertEqual((Entry.objects.count(), DailyUse.objects.count()), (0, 0))

    def test_privacy_mentions_usage_record(self):
        r = Client().get("/privacy")
        self.assertContains(r, "새 고티처 이용 기록")
        self.assertContains(r, "기록한 날부터 1년이 지나면 파기")
