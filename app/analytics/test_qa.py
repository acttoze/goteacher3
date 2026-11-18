"""V10 QA 경계 사례(구현 테스트 tests.py에 없는 것만).

- 위조 입장권 기록은 IP당 1시간 20건까지, 응답은 넘어도 그대로(로그인 화면으로).
- 다른 계정 확인 화면 → 확인(POST)하면 switch_shown 다음 성공 한 건.
- 업체·TV·폰·정적 경로, JSON 응답, 오류 응답은 세지 않는다.
- 취합 보기처럼 key가 든 주소는 모양만 남는다.
- /analytics: 멈춘 운영자 404, 기간 버튼 표시, 회원 이름 없음.
- purge: 보관 기간 안의 기록은 남고, 미리 보기(dry-run)는 안 지운다.
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from django.utils import timezone

from accounts.test_legacy_sso import KEY, MB_NO, REDIS_STANDIN, ticket
from collect.models import Collection
from feedback.models import Feedback
from hwpfiles.retention import purge
from legacy.models import LegacyMap
from spaces.models import Member, School, Space

from . import middleware
from .models import DailyUse, Entry
from .stats import build
from .tests import school, seat

User = get_user_model()
FORGED = "other-key-0123456789abcdef0123456789"


@REDIS_STANDIN
@override_settings(LEGACY_SSO_KEY=KEY)
class EntryEdgeTests(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        self.space = school("새솔초", "1234567")
        self.m = seat(self.space, "김옛날", username="OldKim")
        LegacyMap.objects.create(kind="user", legacy_id=str(MB_NO), obj_id=self.m.user.pk)

    def test_forged_log_capped_20_per_ip_and_response_unchanged(self):
        for _ in range(25):
            r = self.client.get("/auth/legacy", {"t": ticket(key=FORGED)}, REMOTE_ADDR="10.0.0.1")
            self.assertRedirects(r, "/start", fetch_redirect_response=False)
        self.assertEqual(Entry.objects.filter(reason="bad_ticket").count(), 20)
        self.client.get("/auth/legacy", {"t": ticket(key=FORGED)}, REMOTE_ADDR="10.0.0.2")  # 다른 IP는 따로 센다
        self.assertEqual(Entry.objects.filter(reason="bad_ticket").count(), 21)

    def test_expired_and_garbage_tickets_are_bad_ticket(self):
        self.client.get("/auth/legacy", {"t": ticket(exp=1)})
        self.client.get("/auth/legacy", {"t": "not-a-ticket"})
        self.client.get("/auth/legacy")
        self.assertEqual(list(Entry.objects.values_list("ok", "reason", "user")), [(False, "bad_ticket", None)] * 3)

    def test_switch_then_confirm_records_both(self):
        c = Client()
        c.force_login(User.objects.create(username="google:other"))
        t = ticket()
        self.assertEqual(c.get("/auth/legacy", {"t": t}).status_code, 200)
        r = c.post("/auth/legacy", {"t": t})
        self.assertRedirects(r, f"/s/{self.space.token}/today", fetch_redirect_response=False)
        self.assertEqual(list(Entry.objects.order_by("pk").values_list("ok", "reason")), [(False, "switch_shown"), (True, "")])

    def test_success_without_school_space(self):
        self.m.delete()
        r = self.client.get("/auth/legacy", {"t": ticket()})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        e = Entry.objects.get()
        self.assertEqual((e.ok, e.space), (True, None))


class CountsFilterTests(TestCase):
    """미들웨어의 '세는가' 판정을 요청·응답 모양별로 직접 본다(로그인한 계정 회원 기준)."""

    def setUp(self):
        self.space = school("새솔초", "1234567")
        self.m = seat(self.space, "사일")

    def counts(self, path, response=None, method="get", **headers):
        req = getattr(RequestFactory(), method)(path, headers=headers)
        req.user = self.m.user
        try:
            req.resolver_match = resolve(path.split("?")[0])
        except Exception:
            req.resolver_match = None
        return middleware._counts(req, response or HttpResponse("<p>x</p>", content_type="text/html; charset=utf-8"))

    def test_base_case_counts(self):
        self.assertTrue(self.counts(f"/s/{self.space.token}/today"))

    def test_skipped_paths(self):
        for path in ("/v/", "/v/abc", "/tv/abc", "/m/abc", "/ops/feedback", "/analytics", "/healthz", "/static/x.css"):
            self.assertFalse(self.counts(path), path)

    def test_non_html_error_post_htmx(self):
        today = f"/s/{self.space.token}/today"
        self.assertFalse(self.counts(today, JsonResponse({"a": 1})))
        self.assertFalse(self.counts(today, HttpResponse("x", status=500, content_type="text/html")))
        self.assertFalse(self.counts(today, HttpResponse("x", status=302, content_type="text/html")))
        self.assertFalse(self.counts(today, method="post"))
        self.assertFalse(self.counts(today, HX_Request="true"))


class KeyRouteTests(TestCase):
    def test_collection_key_not_stored(self):
        space = school("새솔초", "1234567")
        m = seat(space, "사일")
        col = Collection.objects.create(space=space, created_by=m, title="연수 희망", source="table")
        test = self

        class C(Client):
            def request(self, **req):
                with test.captureOnCommitCallbacks(execute=True):
                    return super().request(**req)
        c = C()
        c.force_login(m.user)
        r = c.get(f"/s/{space.token}/c/{col.key}/?sort=name")
        self.assertEqual(r.status_code, 200)
        u = DailyUse.objects.get()
        self.assertEqual((u.route, u.space, u.n), ("/s/<공간>/c/<key>/", space, 1))
        for v in (col.key, col.link_token, space.token, "sort", "name"):
            self.assertNotIn(v, u.route)


class PageEdgeTests(TestCase):
    def setUp(self):
        self.a = school("가람초", "A1")
        self.names = ["홍길동", "김철수", "이영희"]
        self.members = [seat(self.a, n, "g1") for n in self.names]
        for i, m in enumerate(self.members):
            DailyUse.objects.create(day=timezone.localdate() - timedelta(days=i), user=m.user, space=self.a, route="/s/<공간>/today", n=1)
            Entry.objects.create(user=m.user, space=self.a, ok=True)
        self.staff = User.objects.create(username="google:ops", first_name="운영", is_staff=True)

    def get(self, user, **q):
        c = Client()
        c.force_login(user)
        return c.get("/analytics", q)

    def test_inactive_staff_404_and_post_rejected(self):
        off = User.objects.create(username="google:off", is_staff=True, is_active=False)
        c = Client()
        c.force_login(off)
        self.assertEqual(c.get("/analytics").status_code, 404)
        c.force_login(self.staff)
        self.assertEqual(c.post("/analytics").status_code, 405)

    def test_periods_and_no_names(self):
        for d, label in (("1", "오늘"), ("7", "7일"), ("30", "30일"), ("all", "전체")):
            r = self.get(self.staff, d=d)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, f'value="{d}" aria-pressed="true">{label}<', html=False)
            for n in self.names:
                self.assertNotContains(r, n)
        self.assertEqual(self.get(self.staff, d="1").context["tiles"][2][1], 1)
        self.assertEqual(self.get(self.staff, d="7").context["tiles"][2][1], 3)
        self.assertContains(self.get(self.staff, d="x"), 'value="7" aria-pressed="true"')  # 모르는 값은 7일

    def test_no_old_brand(self):
        self.assertNotContains(self.get(self.staff), "티로드")


class PurgeEdgeTests(TestCase):
    def setUp(self):
        self.m = seat(school("가람초", "A1"), "가1")
        self.now = timezone.now()

    def test_inside_keep_window_stays(self):
        e = Entry.objects.create(user=self.m.user, ok=True)
        Entry.objects.filter(pk=e.pk).update(at=self.now - timedelta(days=364))
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=364), user=self.m.user, route="/help", n=1)
        self.assertEqual(purge(self.now)["analytics"], 0)
        self.assertEqual((Entry.objects.count(), DailyUse.objects.count()), (1, 1))

    def test_dry_run_keeps(self):
        e = Entry.objects.create(ok=False, reason="bad_ticket")
        Entry.objects.filter(pk=e.pk).update(at=self.now - timedelta(days=400))
        self.assertEqual(purge(self.now, dry_run=True)["analytics"], 1)
        self.assertEqual(Entry.objects.count(), 1)

    @override_settings(ANALYTICS_KEEP_DAYS=30)
    def test_short_keep(self):
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=31), user=self.m.user, route="/help", n=1)
        self.assertEqual(purge(self.now)["analytics"], 1)
        self.assertFalse(DailyUse.objects.exists())

    def test_purge_command_prints_analytics(self):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        with mock.patch("hwpfiles.retention.timezone.now", return_value=self.now):
            call_command("purge", "--dry-run", stdout=out)
        self.assertIn("지난 이용 기록 0건", out.getvalue())


class OrgTargetTests(TestCase):
    """V10 보정: 이관 기관(org)도 대상. 그룹 키는 기관마다 따로, 학교 소속 표시·순서는 그대로."""

    def org(self, name, code, groups, legacy=True):
        s = School.objects.create(neis_atpt="", neis_code="", name=name)
        if legacy:
            LegacyMap.objects.create(kind="school", legacy_id=code, obj_id=s.pk)
        return Space.objects.create(school=s, kind="org", grade=0, name=name, org_groups=[{"k": k, "n": n} for k, n in groups])

    def use(self, m, days_ago=0):
        DailyUse.objects.create(day=timezone.localdate() - timedelta(days=days_ago), user=m.user, space=m.space, route="/s/<공간>/today", n=1)

    def setUp(self):
        # 기관을 먼저 만든다: 자리 순서가 앞서도 학교 소속 줄의 이름·순서가 흔들리면 안 된다
        self.org_a = self.org("가기관", "9000003", [("t1", "혁신팀"), ("t2", "장학팀")])
        self.org_b = self.org("나기관", "9000004", [("t1", "연수팀")])
        self.a1 = seat(self.org_a, "기관일", "t1")
        self.a2 = seat(self.org_a, "기관이", "t2")
        self.b1 = seat(self.org_b, "기관삼", "t1")
        self.school = school("가람초", "A1")
        self.s1 = seat(self.school, "학교일", "office")
        self.s2 = seat(self.school, "학교이", "g1")

    def groups(self, data):
        return [(g["label"], g["target"], g["active"]) for g in data["groups"]]

    def tiles(self, data):
        return {label: n for label, n, _ in data["tiles"]}

    def test_org_member_on_every_surface(self):
        self.use(self.a1)
        self.use(self.a1, 1)
        self.use(self.b1, 20)  # 들어와 봤지만 이 기간엔 안 씀
        Feedback.objects.create(space=self.org_a, member=self.a1, kind="bad", text="기관 의견")
        data = build(7)
        t = self.tiles(data)
        self.assertEqual((t["대상 회원"], t["들어와 본 사람"], t["기간 이용자"], t["다시 온 사람"], t["받은 의견"]), (5, 2, 1, 1, 1))
        self.assertEqual([(label, n) for label, n, _ in data["funnel"]], [("대상 회원", 5), ("들어와 봄", 2), ("다시 옴", 1), ("의견 남김", 1)])
        rows = {r["name"]: (r["target"], r["entered"], r["active"], r["feedback"]) for r in data["schools"]}
        self.assertEqual(rows, {"가기관": (2, 1, 1, 1), "나기관": (1, 1, 0, 0), "가람초": (2, 0, 0, 0)})

    def test_same_key_in_two_orgs_stays_apart(self):
        self.use(self.a1)
        got = self.groups(build(7))
        self.assertIn(("혁신팀", 1, 1), got)
        self.assertIn(("연수팀", 1, 0), got)
        self.assertIn(("장학팀", 1, 0), got)

    def test_school_groups_keep_label_and_order_first(self):
        got = [g[0] for g in self.groups(build(7))]
        self.assertEqual(got[:2], ["1학년", "교무실"])
        self.assertCountEqual(got[2:], ["혁신팀", "장학팀", "연수팀"])

    def test_unassigned_org_member_joins_one_no_group_row(self):
        seat(self.org_a, "미배정", "")
        seat(self.school, "무소속", "")
        got = self.groups(build(7))
        self.assertEqual([g for g in got if g[0] == "소속 없음"], [("소속 없음", 2, 0)])

    def test_org_seat_with_school_shaped_key_stays_out_of_school_row(self):
        """기관 자리의 group이 g4여도 학교 '4학년' 줄과 섞이지 않고, 그 기관엔 없는 키라 '소속 없음'이다. 자리 순서와 무관."""
        seat(self.org_a, "키겹침", "g4")  # 학교 자리보다 먼저
        seat(self.school, "사학년", "g4")
        seat(self.org_b, "키겹침둘", "g4")  # 학교 자리보다 나중
        got = self.groups(build(7))
        self.assertIn(("4학년", 1, 0), got)
        self.assertIn(("소속 없음", 2, 0), got)

    def test_outsiders_stay_out(self):
        before = self.tiles(build(7))["대상 회원"]
        plain = self.org("새기관", "X", [("t1", "외부팀")], legacy=False)
        LegacyMap.objects.filter(obj_id=plain.school_id).delete()
        seat(plain, "외부")
        other = self.org("유저맵기관", "Y", [("t1", "맵팀")], legacy=False)
        LegacyMap.objects.create(kind="user", legacy_id="77", obj_id=other.school_id)  # kind가 school이 아니면 이관 학교가 아니다
        seat(other, "맵", "t1")
        grade = Space.objects.create(school=self.school.school, kind="grade", grade=4, name="4학년")
        seat(grade, "동학년")
        Member.objects.create(space=self.org_a, user=User.objects.create(username="guest:o"), name="손님", cls="미배정", group="t1")
        seat(self.org_a, "운영", "t1", is_staff=True)
        data = build(7)
        self.assertEqual(self.tiles(data)["대상 회원"], before)
        self.assertEqual({r["name"] for r in data["schools"]}, {"가기관", "나기관", "가람초"})
        self.assertEqual(dict((g[0], g[1]) for g in self.groups(data))["혁신팀"], 1)
        self.assertFalse({"외부팀", "맵팀"} & {g[0] for g in self.groups(data)})

    def test_page_and_command_with_org_data(self):
        self.use(self.a1)
        Feedback.objects.create(space=self.org_a, member=self.a1, kind="bad", text="기관 의견")
        staff = User.objects.create(username="google:ops", is_staff=True)
        c = Client()
        c.force_login(staff)
        for d in ("1", "7", "30", "all"):
            r = c.get("/analytics", {"d": d})
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "혁신팀")
            self.assertNotContains(r, "기관일")
        out = StringIO()
        call_command("analytics", "--since", "7d", stdout=out)
        self.assertIn("대상 회원: 5", out.getvalue())
        self.assertIn("가기관 | 2 | 1 | 50%", out.getvalue())

    def test_queries_do_not_grow_with_orgs(self):
        self.use(self.a1)
        staff = User.objects.create(username="google:ops", is_staff=True)
        c = Client()
        c.force_login(staff)
        with CaptureQueriesContext(connection) as few:
            c.get("/analytics")
        for i in range(5):
            o = self.org(f"추가{i}", f"80{i}", [("t1", f"팀{i}")])
            self.use(seat(o, f"추가회원{i}", "t1"))
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(c.get("/analytics").status_code, 200)
        self.assertEqual(len(many), len(few))
        self.assertLess(len(many), 40)


class InternalAccountTests(TestCase):
    """내부 시험 계정 moon: 운영자 권한이 없어도 어떤 숫자에도 안 잡힌다. 넣기 전과 넣은 뒤 집계가 통째로 같아야 한다."""

    def setUp(self):
        self.a = school("가람초", "A1")
        self.t = seat(self.a, "교사", "g1")
        today = timezone.localdate()
        DailyUse.objects.create(day=today, user=self.t.user, space=self.a, route="/s/<공간>/today", n=1)
        Entry.objects.create(user=self.t.user, space=self.a, ok=True)
        Feedback.objects.create(space=self.a, member=self.t, kind="bad", text="교사 의견")

    def add_moon(self):
        today = timezone.localdate()
        lab = school("대아초", "7731021")
        moon = seat(lab, "시험", "g4", username="moon")
        shared = Member.objects.create(space=self.a, user=moon.user, name="시험", cls="5-1", group="g5")  # 다른 학교에 같이 있어도
        for sp, m in ((lab, moon), (self.a, shared)):
            for back in (0, 1):
                DailyUse.objects.create(day=today - timedelta(days=back), user=m.user, space=sp, route="/s/<공간>/board", n=50)
            Feedback.objects.create(space=sp, member=m, kind="bug", text="시험 의견")
            Feedback.objects.create(space=sp, member=m, kind="back_slow")
        Entry.objects.create(user=moon.user, space=lab, ok=True)
        for reason in ("locked", "replayed", "switch_shown"):
            Entry.objects.create(user=moon.user, ok=False, reason=reason)

    def test_every_surface_is_unchanged_by_moon(self):
        for days in (1, 7, None):
            before = build(days)
            with transaction.atomic():
                self.add_moon()
                self.assertEqual(build(days), before, days)
                transaction.set_rollback(True)

    def test_moon_only_school_hidden_on_page_and_command(self):
        self.add_moon()
        staff = User.objects.create(username="google:ops", is_staff=True)
        c = Client()
        c.force_login(staff)
        r = c.get("/analytics")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "대아초")
        self.assertNotContains(r, "시험 의견")
        out = StringIO()
        call_command("analytics", "--since", "7d", stdout=out)
        self.assertNotIn("대아초", out.getvalue())
        self.assertIn("대상 회원: 1", out.getvalue())

    def test_unknown_account_failure_and_orphan_feedback_still_count(self):
        self.add_moon()
        Entry.objects.create(user=None, ok=False, reason="bad_ticket")
        Entry.objects.create(user=None, ok=False, reason="replayed")
        orphan = Feedback.objects.create(space=self.a, member=None, kind="wish", text="나간 분 의견")
        back = Feedback.objects.create(space=self.a, member=None, kind="back_data")
        data = build(7)
        t = {label: n for label, n, _ in data["tiles"]}
        self.assertEqual((t["입장 실패"], t["받은 의견"], t["예전 고티처로"]), (2, 2, 1))
        self.assertIn(orphan, data["recent"])
        self.assertIn(back, data["recent"])
        row = next(r for r in data["schools"] if r["name"] == "가람초")
        self.assertEqual((row["feedback"], row["back"]), (2, 1))

    def test_similar_usernames_are_not_excluded(self):
        seat(self.a, "비슷", "g1", username="moon2")
        seat(self.a, "구글", "g1", username="google:moon")
        self.add_moon()
        self.assertEqual(build(7)["tiles"][0][1], 3)
