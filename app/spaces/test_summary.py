"""L1 학교 요약 한 장(prototype summaryCard, 26번 4장 교감용 요약).

불변식
- 실제 DB 값으로만, 이번 학기(3~8월·9~2월) 범위. 빈 학교는 0·0%(0으로 나누기 없음).
- 그룹 칸·명부 밖 줄·지운 공지·대상 0명 공지·그룹을 옮긴 사람이 있어도 100%를 넘지 않는다.
- 합계 쿼리 수는 건수와 무관하다.
- 전체 회원이 같은 숫자를 본다(요약 한 장). 공지 제목·사람 이름 없이 합계 숫자만. 학교 화면은 단추만(33번 2).
- 학교 공간에만 있다: 다른 학교·동학년·비회원 404. 만료 학교도 읽기 200.
- 공지 확인율은 대상 합계 10명 미만이면 숨긴다(요약만의 규칙, 현황 화면은 그대로).
- 인쇄는 A4 세로 1쪽: 학교 이름·기간·결재란(담당·교감·교장)·"교감 · 교장 결재용"과 숫자 카드, 안내 문구.
"""
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import Client, SimpleTestCase
from django.test.utils import CaptureQueriesContext

from collect.models import Cell, Collection, Field, Row
from collect.presets import PRESETS
from notices.models import Notice, NoticeSeen

from .merge import open_school
from .models import Member, School, Space
from .summary import figures, period_text, semester
from .test_merge import UNTIL, MergeBase
from .test_school import google

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=KST)
GUIDE = "학생 개인정보는 받지 않고 교직원 업무 정보만 다룹니다"
NOTICE_TITLE = "비밀제목공지"


def at(*args):
    return datetime(*args, tzinfo=KST)


class SemesterTests(SimpleTestCase):
    def test_boundaries(self):
        spring, fall = (date(2026, 3, 1), date(2026, 9, 1)), (date(2026, 9, 1), date(2027, 3, 1))
        for d, want in [(date(2026, 3, 1), spring), (date(2026, 8, 31), spring), (date(2026, 9, 1), fall),
                        (date(2026, 12, 31), fall), (date(2027, 1, 1), fall), (date(2027, 2, 28), fall),
                        (date(2024, 2, 29), (date(2023, 9, 1), date(2024, 3, 1)))]:
            with self.subTest(d):
                self.assertEqual(semester(d), want)


class PeriodTests(SimpleTestCase):
    def test_period_text(self):
        self.assertEqual(period_text(date(2026, 11, 3)), "2026년 9월 ~ 2027년 2월")
        self.assertEqual(period_text(date(2027, 2, 28)), "2026년 9월 ~ 2027년 2월")
        self.assertEqual(period_text(date(2027, 3, 1)), "2027년 3월 ~ 2027년 8월")


class SummaryBase(MergeBase):
    def setUp(self):
        p = mock.patch("django.utils.timezone.now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)  # m4 관리자(g4), m4b(g4), m5(g5). 취합 2건, 2칸 모두 냄
        for m in (self.m4, self.m4b, self.m5):
            m.refresh_from_db()
        self.url = f"/s/{self.hub.token}/school"
        self.print_url = f"{self.url}/summary"

    def fill_group(self, group, n, tag=""):
        return [Member.objects.create(space=self.hub, name=f"{tag}채움{group}{i}", cls="", group=group) for i in range(n)]

    def as_(self, user):
        c = Client()
        c.force_login(user)
        return c

    def collection(self, **kw):
        return Collection.objects.create(space=self.hub, title="취합", source="sheet", **kw)

    def row(self, c, filled=False, **kw):
        kw.setdefault("unit", "칸")
        r = Row.objects.create(collection=c, pos=c.rows.count(), **kw)
        if filled:
            f = c.fields.first() or Field.objects.create(collection=c, pos=0, name="값")
            Cell.objects.create(row=r, field=f, value="1", by="누구")
        return r

    def notice(self, audience="all", must=True, by=None):
        return Notice.objects.create(space=self.hub, title=NOTICE_TITLE, by=by, by_name="쓴사람", audience=audience, must=must)

    def ack(self, n, member, v=1):
        NoticeSeen.objects.create(notice=n, member=member, v=v, acked=True)


class EmptyTests(MergeBase):
    def setUp(self):
        super().setUp()
        s = School.objects.create(neis_atpt="J10", neis_code="7000001", name="빈초등학교", plan="school", paid_until=UNTIL)
        self.empty = Space.objects.create(school=s, kind="school", grade=0, name="빈초등학교")

    def test_all_zero_without_members(self):
        f = figures(self.empty)
        self.assertEqual((f["n_collections"], f["n_open"], f["n_done"], f["n_rows"], f["done_pct"]), (0, 0, 0, 0, 0))
        self.assertEqual((f["ack_pct"], f["n_must"], f["n_members"], f["n_groups"]), (0, 0, 0, 0))

    def test_pages_show_zero(self):
        u = google("first", "첫관리자")
        Member.objects.create(space=self.empty, user=u, name="첫관리자", cls="교무실", group="office", is_manager=True)
        c = Client()
        c.force_login(u)
        r = c.get(f"/s/{self.empty.token}/school/summary")
        self.assertEqual(r.status_code, 200)
        for text in ("0건", "진행 중 0건", "0%", "0/0칸", "0건 평균", "1명", "1개 소속"):
            self.assertContains(r, text)
        # 학교 화면은 건수 0이면 요약 단추도 숨긴다(33번 3)
        r = c.get(f"/s/{self.empty.token}/school")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "학교 업무 요약")
        self.assertNotContains(r, "제출 완료율")


class CollectTests(SummaryBase):
    def test_counts_real_rows(self):
        c = self.collection()
        self.row(c)
        f = figures(self.hub)
        self.assertEqual((f["n_collections"], f["n_open"], f["n_done"], f["n_rows"], f["done_pct"]), (3, 1, 2, 3, 67))

    def test_group_and_outside_rows_never_exceed_100(self):
        c = self.collection(allow_outside=True)
        self.row(c, filled=True, group="g4")
        self.row(c, filled=True, who="명부밖")
        f = figures(self.hub)
        self.assertEqual((f["n_done"], f["n_rows"], f["done_pct"]), (4, 4, 100))
        self.row(c, group="g5")
        f = figures(self.hub)
        self.assertEqual((f["n_done"], f["n_rows"]), (4, 5))
        self.assertLessEqual(f["done_pct"], 100)

    def test_multi_rows_count_once_like_board(self):
        c = self.collection(multi=True)
        self.row(c, filled=True, who="가")
        self.row(c, filled=True, who="가")
        self.row(c, who="가")
        f = figures(self.hub)
        self.assertEqual((f["n_done"], f["n_rows"]), (3, 3))

    def test_matrix_needs_every_input(self):
        c = self.collection(unit_head=PRESETS["training"]["unit_head"])
        Collection.objects.filter(pk=c.pk).update(source="preset")
        a = Field.objects.create(collection=c, pos=0, name="가")
        Field.objects.create(collection=c, pos=1, name="나")
        Field.objects.create(collection=c, pos=2, name="계", kind="calc")
        r = self.row(c, who="일부만")
        Cell.objects.create(row=r, field=a, value="1")
        f = figures(self.hub)
        self.assertEqual((f["n_done"], f["n_rows"]), (2, 3))

    def test_open_excludes_closed_past_due_and_finished(self):
        self.row(self.collection(closed_at=at(2026, 11, 1, 9)))
        self.row(self.collection(due=at(2026, 11, 1, 9)))
        self.row(self.collection(due=at(2026, 11, 20, 9)), filled=True)
        self.assertEqual(figures(self.hub)["n_open"], 0)
        self.row(self.collection(due=at(2026, 11, 20, 9)))
        f = figures(self.hub)
        self.assertEqual((f["n_collections"], f["n_open"]), (6, 1))

    def test_only_this_semester_kst(self):
        old = self.collection()
        Collection.objects.filter(pk=old.pk).update(created_at=at(2026, 8, 31, 23, 59))
        self.row(old)
        first = self.collection()
        Collection.objects.filter(pk=first.pk).update(created_at=at(2026, 9, 1, 0, 1))
        self.assertEqual(figures(self.hub)["n_collections"], 3)
        self.assertEqual(figures(self.hub)["n_rows"], 2)

    def test_other_school_and_old_spaces_not_counted(self):
        before = figures(self.hub)
        c = Collection.objects.create(space=self.far, title="먼 곳", source="sheet")
        Row.objects.create(collection=c, pos=0, unit="칸")
        self.assertEqual(figures(self.hub), before)


class NoticeTests(SummaryBase):
    def setUp(self):
        super().setUp()
        self.g5s = [self.m5, *self.fill_group("g5", 9)]  # 대상 10명: 표시 기준을 넘겨 평균 자체를 본다

    def test_average_of_rates(self):
        a = self.notice()  # 12명 중 10명
        b = self.notice(audience=",g5,")  # 10명 중 10명
        for m in self.g5s:
            self.ack(a, m)
            self.ack(b, m)
        self.notice(must=False)
        f = figures(self.hub)
        self.assertEqual((f["n_must"], f["ack_pct"], f["ack_hidden"]), (2, 92, False))  # (10/12 + 1) / 2

    def test_opened_but_not_acked_is_not_counted(self):
        n = self.notice(audience=",g5,")
        NoticeSeen.objects.create(notice=n, member=self.m5, v=1, acked=False)
        self.assertEqual(figures(self.hub)["ack_pct"], 0)

    def test_acks_on_many_versions_count_once(self):
        n = self.notice(audience=",g5,")
        for v in (1, 2, 3):
            self.ack(n, self.m5, v=v)
        self.assertEqual(figures(self.hub)["ack_pct"], 10)

    def test_moved_group_cannot_exceed_100(self):
        n = self.notice(audience=",g5,")
        for m in self.g5s:
            self.ack(n, m)
        self.ack(n, self.m4)  # 그때는 g5였다가 옮긴 사람
        self.assertEqual(figures(self.hub)["ack_pct"], 100)

    def test_no_target_notice_left_out_of_average(self):
        self.ack(self.notice(audience=",g1,"), self.m5)
        f = figures(self.hub)
        self.assertEqual((f["n_must"], f["ack_pct"], f["ack_hidden"]), (0, 0, False))
        m = self.notice(audience=",g5,")
        for x in self.g5s:
            self.ack(m, x)
        f = figures(self.hub)
        self.assertEqual((f["n_must"], f["ack_pct"]), (1, 100))

    def test_deleted_notice_drops_out(self):
        n = self.notice()
        self.ack(n, self.m5)
        n.delete()
        f = figures(self.hub)
        self.assertEqual((f["n_must"], f["ack_pct"]), (0, 0))

    def test_only_this_semester(self):
        n = self.notice()
        Notice.objects.filter(pk=n.pk).update(created_at=at(2026, 3, 2, 9))
        self.assertEqual(figures(self.hub)["n_must"], 0)


class AckThresholdTests(SummaryBase):
    """요약만의 규칙: 이번 학기 확인 필수 공지의 대상 합계가 10명 미만이면 확인율을 숨긴다(적으면 합계로 개인을 짐작할 수 있다)."""

    REASON = "대상이 적어 표시하지 않아요"

    def test_nine_hidden_ten_shown(self):
        self.fill_group("g6", 9)
        self.notice(audience=",g6,")
        f = figures(self.hub)
        self.assertEqual((f["ack_hidden"], f["ack_pct"]), (True, None))
        self.fill_group("g6", 1, tag="추가")
        f = figures(self.hub)
        self.assertEqual((f["ack_hidden"], f["ack_pct"]), (False, 0))

    def test_sum_across_notices(self):
        self.fill_group("g6", 4)
        self.fill_group("g1", 5)
        self.notice(audience=",g6,")
        self.notice(audience=",g1,")
        self.assertTrue(figures(self.hub)["ack_hidden"])  # 4 + 5 = 9
        self.fill_group("g1", 1, tag="추가")
        self.assertFalse(figures(self.hub)["ack_hidden"])  # 4 + 6 = 10

    def test_old_semester_targets_not_summed(self):
        self.fill_group("g6", 10)
        n = self.notice(audience=",g6,")
        Notice.objects.filter(pk=n.pk).update(created_at=at(2026, 3, 2, 9))
        self.notice(audience=",g5,")
        self.assertTrue(figures(self.hub)["ack_hidden"])

    def test_no_notice_is_zero_not_hidden(self):
        f = figures(self.hub)
        self.assertEqual((f["ack_hidden"], f["ack_pct"], f["n_must"]), (False, 0, 0))

    def test_pages_show_dash_and_reason_without_rate(self):
        self.ack(self.notice(audience=",g5,"), self.m5)  # 1명 중 1명: 숫자가 나가면 그 사람 상태가 드러난다
        c = self.as_(self.u5)
        r = c.get(self.print_url)
        self.assertContains(r, "—")
        self.assertContains(r, self.REASON)
        self.assertNotContains(r, "건 평균")
        self.assertNotRegex(r.content.decode(), r"<b[^>]*>\s*100%\s*</b>\s*(<small[^>]*>[^<]*</small>)?\s*<span>확인 필수")
        self.assertNotContains(c.get(self.url), "확인 필수 공지 확인율")  # 학교 화면에는 숫자가 없다

    def test_ten_targets_show_rate(self):
        self.fill_group("g5", 9)
        self.notice(audience=",g5,")
        r = self.as_(self.u5).get(self.print_url)
        self.assertNotContains(r, self.REASON)
        self.assertContains(r, "1건 평균")


class MembersTests(SummaryBase):
    def test_members_and_groups_in_use(self):
        f = figures(self.hub)
        self.assertEqual((f["n_members"], f["n_groups"]), (3, 2))

    def test_blank_group_not_a_group(self):
        Member.objects.create(space=self.hub, name="소속없음", cls="")
        f = figures(self.hub)
        self.assertEqual((f["n_members"], f["n_groups"]), (4, 2))


class QueryCountTests(SummaryBase):
    def grow(self, n):
        for i in range(n):
            c = self.collection(unit_head=PRESETS["training"]["unit_head"])
            Collection.objects.filter(pk=c.pk).update(source="preset")
            self.row(c, filled=True, who=f"가{i}")
            self.row(c, who=f"나{i}")
            self.ack(self.notice(), self.m5)

    def count(self):
        with CaptureQueriesContext(connection) as q:
            figures(self.hub)
        return len(q)

    def test_same_queries_whatever_the_size(self):
        self.grow(1)
        small = self.count()
        self.grow(30)
        self.assertEqual(self.count(), small)


class PageTests(SummaryBase):
    def test_school_screen_links_to_summary_without_numbers(self):
        """33번 2: 학교 화면은 제목 줄 단추만, 숫자는 요약 쪽에(전체 회원이 같은 값)."""
        n = self.notice(by=self.m4)
        self.ack(n, self.m5)
        for user in (self.u4, self.u5):
            r = self.as_(user).get(self.url)
            self.assertContains(r, f'href="{self.print_url}">학교 업무 요약</a>')
            for hidden in ("고티처로 한 취합", "제출 완료율", GUIDE, "티로드"):
                self.assertNotContains(r, hidden)

    def test_button_stays_after_htmx_swap_and_on_duty_tab(self):
        self.notice(by=self.m4)
        c = self.as_(self.u4)
        self.assertContains(c.get(f"{self.url}?tab=duty"), "학교 업무 요약")
        r = c.post(f"{self.url}/members/{self.m5.key}/manager", {"on": "0"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "학교 업무 요약")

    def test_print_page_head_numbers_and_guide(self):
        n = self.notice(by=self.m4)
        self.ack(n, self.m5)
        for user in (self.u4, self.u5):
            r = self.as_(user).get(self.print_url)
            self.assertEqual(r.status_code, 200)
            body = r.content.decode()
            for secret in (NOTICE_TITLE, "송민재", "정하은", "김도윤", "사학년", "티로드"):
                self.assertNotIn(secret, body)
            for text in ("A4 portrait", "고티처로 한 취합", "제출 완료율", "확인 필수 공지 확인율", "함께 쓰는 교직원", GUIDE,
                         'id="printBtn"', "js/print.js", self.school.name, "2026년 9월 ~ 2027년 2월", "교감 · 교장 결재용",
                         "<th>담당</th><th>교감</th><th>교장</th>"):
                self.assertIn(text, body)
            self.assertIn("no-store", r["Cache-Control"])

    def test_numbers_on_print(self):
        self.row(self.collection())
        r = self.as_(self.u4).get(self.print_url)
        for text in ("3건", "진행 중 1건", "67%", "2/3칸"):
            self.assertContains(r, text)

    def test_other_school_grade_space_and_stranger_404(self):
        stranger = google("stranger", "모르는사람")
        for url in (self.url, self.print_url):
            self.assertEqual(self.as_(self.far_user).get(url).status_code, 404)
            self.assertEqual(self.as_(stranger).get(url).status_code, 404)
        self.assertEqual(self.as_(self.far_user).get(f"/s/{self.far.token}/school/summary").status_code, 404)

    def test_anonymous_gets_nothing(self):
        r = Client().get(self.print_url)
        self.assertIn(r.status_code, (302, 404))
        self.assertNotContains(r, "제출 완료율", status_code=r.status_code)

    def test_expired_school_reads(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        c = self.as_(self.u5)
        self.assertEqual(c.get(self.url).status_code, 200)
        r = c.get(self.print_url)
        self.assertContains(r, "제출 완료율")

    def test_get_only(self):
        self.assertEqual(self.as_(self.u4).post(self.print_url).status_code, 405)
