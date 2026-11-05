"""L1 QA: test_summary.py에 없는 경계만. 학기 경계 네 날(그날 기준으로 돌린 합계), 합본과 같은 판정, 토큰 바꿔 끼우기, 대상 합계 문턱의 빠진 조합."""
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client

from collect.models import Cell, Collection, Field, Row
from collect.presets import PRESETS
from collect.submit import _head
from notices.models import Notice, NoticeSeen

from .merge import open_school
from .models import Member, School, Space
from .summary import figures
from .test_merge import UNTIL, MergeBase
from .test_school import google

KST = ZoneInfo("Asia/Seoul")


def at(*args):
    return datetime(*args, tzinfo=KST)


class QaBase(MergeBase):
    def setUp(self):
        p = mock.patch("django.utils.timezone.now", return_value=at(2026, 11, 3, 10))
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        self.hub = self.new_hub("7600001", "경계초등학교")
        self.boss = self.member(self.hub, "경계관리", "office", manager=True)

    def new_hub(self, code, name, plan="school", until=UNTIL):
        s = School.objects.create(neis_atpt="J10", neis_code=code, name=name, plan=plan, paid_until=until)
        return Space.objects.create(school=s, kind="school", grade=0, name=name)

    def member(self, space, name, group, manager=False):
        return Member.objects.create(space=space, user=google(f"qa{Member.objects.count()}", name), name=name, cls="",
                                     group=group, is_manager=manager)

    def fill(self, group, n, tag=""):
        return [Member.objects.create(space=self.hub, name=f"{tag}{group}{i}", cls="", group=group) for i in range(n)]

    def collection(self, created=None, **kw):
        c = Collection.objects.create(space=self.hub, title="취합", source=kw.pop("source", "sheet"), **kw)
        if created:
            Collection.objects.filter(pk=c.pk).update(created_at=created)
        return c

    def notice(self, created=None, audience="all", by=None):
        n = Notice.objects.create(space=self.hub, title="공지", by=by, by_name="쓴사람", audience=audience, must=True)
        if created:
            Notice.objects.filter(pk=n.pk).update(created_at=created)
        return n


class SemesterBoundaryFigures(QaBase):
    """그날이 '오늘'일 때, 경계 바로 앞뒤에 만든 건 중 무엇이 세어지나(KST 초 단위)."""

    STAMPS = [at(2026, 2, 28, 23, 59, 59), at(2026, 3, 1, 0, 0, 0), at(2026, 8, 31, 23, 59, 59), at(2026, 9, 1, 0, 0, 0),
              at(2027, 2, 28, 23, 59, 59), at(2027, 3, 1, 0, 0, 0)]

    def setUp(self):
        super().setUp()
        self.fill("g1", 12)
        for i, when in enumerate(self.STAMPS):
            c = self.collection(when)
            for k in range(10 ** i):  # 칸 수를 자릿수로: 어느 건이 섞였는지 합계만 보고 안다
                if i < 4:
                    Row.objects.create(collection=c, pos=k, unit="칸")
            self.notice(when)

    def test_each_boundary_day(self):
        # 칸 수: 2/28=1, 3/1=10, 8/31=100, 9/1=1000, 뒤 둘은 0
        cases = [(at(2026, 2, 28, 12), 1, 1), (at(2026, 3, 1, 12), 110, 2), (at(2026, 8, 31, 12), 110, 2),
                 (at(2026, 9, 1, 12), 1000, 2), (at(2027, 2, 28, 12), 1000, 2), (at(2027, 3, 1, 12), 0, 1)]
        for now, n_rows, n_collections in cases:
            with self.subTest(now), mock.patch("django.utils.timezone.now", return_value=now):
                f = figures(self.hub)
                self.assertEqual((f["n_rows"], f["n_collections"], f["n_must"]), (n_rows, n_collections, n_collections))

    def test_midnight_kst_is_the_edge_not_utc(self):
        # 8/31 23:59:59 KST와 9/1 00:00:00 KST는 UTC로는 같은 8/31이다
        with mock.patch("django.utils.timezone.now", return_value=at(2026, 9, 1, 0, 0, 0)):
            self.assertEqual(figures(self.hub)["n_rows"], 1000)
        with mock.patch("django.utils.timezone.now", return_value=at(2026, 8, 31, 23, 59, 59)):
            self.assertEqual(figures(self.hub)["n_rows"], 110)

    def test_printed_period_follows_today(self):
        for now, text in [(at(2027, 2, 28, 12), "2026년 9월 ~ 2027년 2월"), (at(2027, 3, 1, 12), "2027년 3월 ~ 2027년 8월"),
                          (at(2026, 8, 31, 12), "2026년 3월 ~ 2026년 8월"), (at(2026, 9, 1, 12), "2026년 9월 ~ 2027년 2월")]:
            with self.subTest(now), mock.patch("django.utils.timezone.now", return_value=now):
                c = Client()  # 로그인도 그날: 세션 만료가 시계를 따른다
                c.force_login(self.boss.user)
                self.assertContains(c.get(f"/s/{self.hub.token}/school/summary"), text)


class SameAsBoard(QaBase):
    """합계는 취합 화면(_head)의 칸을 모은 값이어야 한다. need를 미리 세는 길과 줄마다 세는 길이 어긋나면 여기서 잡힌다."""

    def cell(self, row, field):
        Cell.objects.create(row=row, field=field, value="1", by="누구")

    def assertMatchesBoard(self):
        heads = [_head(c) for c in Collection.objects.in_space(self.hub)]
        f = figures(self.hub)
        self.assertEqual((f["n_done"], f["n_rows"]), (sum(h["n_done"] for h in heads), sum(h["n_total"] for h in heads)))
        self.assertLessEqual(f["done_pct"], 100)
        return f

    def test_mixed_collections(self):
        plain = self.collection()
        a = Field.objects.create(collection=plain, pos=0, name="값")
        self.cell(Row.objects.create(collection=plain, pos=0, unit="칸", who="가"), a)
        Row.objects.create(collection=plain, pos=1, unit="칸", who="나")

        group = self.collection(allow_outside=True, unit_head=["그룹"])
        b = Field.objects.create(collection=group, pos=0, name="값")
        self.cell(Row.objects.create(collection=group, pos=0, unit="1학년", group="g1"), b)
        Row.objects.create(collection=group, pos=1, unit="2학년", group="g2")
        self.cell(Row.objects.create(collection=group, pos=2, unit="밖", who="명부밖"), b)

        multi = self.collection(multi=True)
        c = Field.objects.create(collection=multi, pos=0, name="값")
        Row.objects.create(collection=multi, pos=0, unit="가", who="가")
        self.cell(Row.objects.create(collection=multi, pos=1, unit="가", who="가"), c)  # 첫 줄이 비어도 받는 곳은 냄
        Row.objects.create(collection=multi, pos=2, unit="나", who="나")

        matrix = self.collection(source="preset", unit_head=PRESETS["training"]["unit_head"])
        d1 = Field.objects.create(collection=matrix, pos=0, name="가")
        d2 = Field.objects.create(collection=matrix, pos=1, name="나")
        Field.objects.create(collection=matrix, pos=2, name="계", kind="calc")
        full = Row.objects.create(collection=matrix, pos=0, unit="칸", who="다")
        self.cell(full, d1)
        self.cell(full, d2)
        self.cell(Row.objects.create(collection=matrix, pos=1, unit="칸", who="일부"), d1)

        f = self.assertMatchesBoard()
        self.assertEqual((f["n_done"], f["n_rows"]), (1 + 2 + 1 + 1, 2 + 3 + 2 + 2))

    def test_matrix_without_inputs_and_empty_collection(self):
        only_calc = self.collection(source="preset", unit_head=PRESETS["training"]["unit_head"])
        Field.objects.create(collection=only_calc, pos=0, name="계", kind="calc")
        Row.objects.create(collection=only_calc, pos=0, unit="칸", who="가")
        self.collection()
        f = self.assertMatchesBoard()
        self.assertEqual(f["n_collections"], 2)

    def test_withdrawn_cells_are_not_done(self):
        c = self.collection()
        f = Field.objects.create(collection=c, pos=0, name="값")
        r = Row.objects.create(collection=c, pos=0, unit="칸", who="가")
        self.cell(r, f)
        self.assertEqual(self.assertMatchesBoard()["n_done"], 1)
        r.cells.all().delete()
        self.assertEqual(self.assertMatchesBoard()["n_done"], 0)


class TokenSwap(QaBase):
    def as_(self, user):
        c = Client()
        c.force_login(user)
        return c

    def urls(self, token):
        return f"/s/{token}/school", f"/s/{token}/school/summary"

    def test_other_school_hub_both_ways(self):
        other = self.new_hub("7600002", "다른초등학교")
        them = self.member(other, "다른관리", "office", manager=True)
        for user, token in ((self.boss.user, other.token), (them.user, self.hub.token)):
            for url in self.urls(token):
                self.assertEqual(self.as_(user).get(url).status_code, 404)

    def test_same_school_grade_member_and_grade_token(self):
        grade = Space.objects.create(school=self.hub.school, grade=5, name="경계초 5학년")
        teacher = Member.objects.create(space=grade, user=google("g5", "오학년"), name="오학년", cls="5-1")
        for url in self.urls(self.hub.token):
            self.assertEqual(self.as_(teacher.user).get(url).status_code, 404)
        for user in (teacher.user, self.boss.user):
            for url in self.urls(grade.token):
                self.assertEqual(self.as_(user).get(url).status_code, 404)

    def test_old_grade_token_after_merge_gives_stranger_nothing(self):
        hub = open_school(self.school, UNTIL, self.m4)
        stranger = google("stranger2", "모르는사람")
        for token in (self.g4.token, hub.token):
            for url in self.urls(token):
                r = self.as_(stranger).get(url)
                self.assertEqual(r.status_code, 404, url)
                r = self.as_(self.far_user).get(url)
                self.assertEqual(r.status_code, 404, url)

    def test_free_plan_school_still_reads(self):
        s = self.new_hub("7600003", "무료초등학교", plan="free", until=date(2020, 1, 1))
        m = self.member(s, "무료관리", "office", manager=True)
        for url in self.urls(s.token):
            self.assertEqual(self.as_(m.user).get(url).status_code, 200)


class AckEdges(QaBase):
    def test_no_target_notice_does_not_hide_or_count(self):
        self.notice(audience=",g6,")
        f = figures(self.hub)
        self.assertEqual((f["ack_hidden"], f["ack_pct"], f["n_must"]), (False, 0, 0))

    def test_no_target_notice_added_to_small_one_stays_hidden(self):
        self.fill("g1", 9)
        self.notice(audience=",g1,")
        self.notice(audience=",g6,")
        self.assertTrue(figures(self.hub)["ack_hidden"])

    def test_deleted_notice_targets_leave_the_sum(self):
        self.fill("g1", 9)
        self.notice(audience=",g1,")
        big = self.notice()  # 전원 10명 → 합계 19
        self.assertFalse(figures(self.hub)["ack_hidden"])
        big.delete()
        self.assertTrue(figures(self.hub)["ack_hidden"])

    def test_member_leaving_can_drop_below_threshold(self):
        ms = self.fill("g1", 10)
        n = self.notice(audience=",g1,")
        NoticeSeen.objects.create(notice=n, member=ms[0], v=1, acked=True)
        self.assertEqual(figures(self.hub)["ack_pct"], 10)
        ms[1].delete()
        f = figures(self.hub)
        self.assertEqual((f["ack_hidden"], f["ack_pct"]), (True, None))

    def test_writer_counts_only_inside_audience(self):
        self.fill("g1", 10)
        self.notice(audience=",g1,", by=self.boss)  # 쓴 사람은 교무실: 대상 밖
        self.assertEqual(figures(self.hub)["ack_pct"], 0)
        mine = self.fill("office", 9)  # 교무실 10명(쓴 사람 포함)
        self.notice(audience=",office,", by=self.boss)
        f = figures(self.hub)
        self.assertEqual(f["ack_pct"], 5)  # (0/10 + 1/10) / 2
        self.assertEqual(len(mine) + 1, 10)

    def test_never_above_100_when_everyone_acks_everything(self):
        ms = self.fill("g1", 10)
        n = self.notice(audience=",g1,", by=ms[0])
        for m in [*ms, self.boss]:
            NoticeSeen.objects.create(notice=n, member=m, v=1, acked=True)
        self.assertEqual(figures(self.hub)["ack_pct"], 100)
