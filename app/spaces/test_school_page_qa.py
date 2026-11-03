"""H3 독립 검증(gt-qa): test_school_page.py에 없는 경계만. 다른 학교 '학교 공간', 표 붙여넣기 모양, 40명 규모, 플랜 경계."""
from datetime import timedelta
from pathlib import Path

from django.db import connection
from django.test import Client, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import new_guest
from collect.paste import PasteError, html_to_tsv

from .duty_paste import read_duties
from .merge import open_school
from .models import Duty, Member, MemberLog, School, Space
from .school import _dept_cards
from .test_merge import UNTIL
from .test_school_page import TSV, Base, google

FIXTURES = Path(__file__).resolve().parent.parent / "collect" / "fixtures" / "paste"
HEAD = "<tr><td>부서</td><td>업무</td><td>담당</td></tr>"
PATHS = ("/school", "/school/me", "/school/duties")


def table(*rows):
    return "<table>" + "".join(rows) + "</table>"


def read_html(html):
    return read_duties(html_to_tsv(html))


class HtmlTableShapes(SimpleTestCase):
    """한글·엑셀 클립보드 HTML 모양. 실제 한글 복사본이 아니라 13번 표본과 같은 형식으로 만든 것."""

    def test_merged_who_fills_every_task(self):
        d = read_html(table(HEAD, "<tr><td rowspan=3>연구부</td><td>수업공개</td><td rowspan=2>가</td></tr>",
                            "<tr><td>연수</td></tr>", "<tr><td>평가</td><td>나</td></tr>"))
        self.assertEqual(d["rows"], [{"dept": "연구부", "task": "수업공개", "who": "가"},
                                     {"dept": "연구부", "task": "연수", "who": "가"},
                                     {"dept": "연구부", "task": "평가", "who": "나"}])

    def test_dept_merged_over_two_columns_in_header(self):
        head = "<tr><td colspan=2>부서</td><td>업무</td><td>담당</td></tr>"
        d = read_html(table(head, "<tr><td rowspan=2>교무부</td><td>기획</td><td>학사</td><td>가</td></tr>",
                            "<tr><td>교육과정</td><td>평가</td><td>나</td></tr>"))
        self.assertEqual(d["rows"], [{"dept": "교무부", "task": "학사", "who": "가"}, {"dept": "교무부", "task": "평가", "who": "나"}])
        # 아래 칸이 병합 없이 갈라지면 2줄 머리글로 읽힌다(모으기와 같은 규칙). 틀린 값을 넣지 않고 오류로 알린다
        with self.assertRaises(PasteError):
            read_html(table(head, "<tr><td>교무부</td><td>기획</td><td>학사</td><td>가</td></tr>"))

    def test_hancom_markup_uppercase_nbsp_spaced_header(self):
        cell = "<TD><P CLASS=HStyle0><SPAN STYLE='font-family:\"맑은 고딕\"'>{}</SPAN></P></TD>"
        row = lambda *c: "<TR>" + "".join(cell.format(x) for x in c) + "</TR>"  # noqa: E731
        html = ("<HTML><BODY><!--StartFragment--><P>2027 업무분장</P><TABLE border=\"1\">"
                + row("부&nbsp;&nbsp;서", "담 당", "업&nbsp;무") + row("연구부", "가", "수업&nbsp;공개") + "</TABLE></BODY></HTML>")
        self.assertEqual(read_html(html)["rows"], [{"dept": "연구부", "task": "수업 공개", "who": "가"}])

    def test_several_paragraphs_in_who_cell_stay_separate_names(self):
        d = read_html(table(HEAD, "<tr><td>연구부</td><td>연수</td><td><p>가나</p><p>다라</p></td></tr>"))
        self.assertEqual(d["rows"][0]["who"], "가나 다라")

    def test_broken_markup_does_not_crash(self):
        self.assertEqual(html_to_tsv("<table><tr><td>부서<td>업무<td>담당<tr><td>가<td>나<td>다"), "")  # 닫지 않은 표는 표 없음
        html = table(HEAD, "<tr><td rowspan=abc colspan=-3>가</td><td rowspan=99999>나</td><td colspan=99999>다</td></tr>")
        self.assertEqual(read_html(html)["rows"][0]["dept"], "가")

    def test_every_real_form_fixture_reads_or_says_why(self):
        """업무분장이 아닌 실제 표를 잘못 붙여도 오류 문구이거나 줄이 나오고, 다른 예외는 없다."""
        for f in sorted(FIXTURES.glob("*.html")):
            try:
                read_html(f.read_text())
            except PasteError:
                pass


class OddText(SimpleTestCase):
    def errors(self, text):
        with self.assertRaises(PasteError, msg=repr(text)):
            read_duties(text)

    def test_nothing_useful(self):
        for text in (" ", "\n\n", "\t\t\n\t\t", "안녕하세요", "가, 나, 다\n라, 마, 바", "…\t…\t…", "부서\t업무\t담당\n…\t…\t…",
                     "합계\t\t\n소계\t\t", "1\t2\n3\t4", "x" * 300_001):
            self.errors(text)

    def test_exactly_500_rows_pass(self):
        self.assertEqual(len(read_duties("부서\t업무\t담당\n" + "부\t일\t가\n" * 500)["rows"]), 500)

    def test_title_within_five_lines_only(self):
        title = "제목\t\t\n" * 4
        self.assertEqual(len(read_duties(title + "부서\t업무\t담당\n부\t일\t가")["rows"]), 1)
        self.errors("제목\t\t\t\n" * 5 + "부서\t업무\t담당\n부\t일\t가")

    def test_long_cells_fit_columns_and_control_chars_gone(self):
        r = read_duties(f"부서\t업무\t담당\n{'가' * 300}\t{'나' * 300}\t{'다' * 300}\n부\x00서\t일‮\t사\x07람")["rows"]
        self.assertEqual((len(r[0]["dept"]), len(r[0]["task"]), len(r[0]["who"])), (100, 200, 100))
        self.assertEqual(r[1], {"dept": "부 서", "task": "일", "who": "사 람"})

    def test_crlf_and_trailing_blank_lines(self):
        self.assertEqual(len(read_duties("부서\t업무\t담당\r\n부\t일\t가\r\n\r\n")["rows"]), 1)

    def test_blank_dept_at_top_is_counted_not_guessed(self):
        d = read_duties("부서\t업무\t담당\n\t일\t가\n부\t일\t나")
        self.assertEqual((len(d["rows"]), d["skipped"]), (1, 1))


class OtherSchoolSpace(Base):
    """test_school_page는 다른 학교가 '동학년 공간'인 경우만 본다. 여기는 양쪽 다 학교 공간."""

    def setUp(self):
        super().setUp()
        self.other_school = School.objects.create(neis_atpt="C10", neis_code="7770001", name="다른초등학교")
        grade = Space.objects.create(school=self.other_school, grade=3, name="다른초 3학년")
        self.other_user = google("other", "다른관리자")
        seat = Member.objects.create(space=grade, user=self.other_user, name="다른관리자", cls="3-1")
        self.other = open_school(self.other_school, UNTIL, seat)
        self.other_base = f"/s/{self.other.token}"
        self.other_member = Member.objects.get(space=self.other, user=self.other_user)
        self.outsider = Client()
        self.outsider.force_login(self.other_user)

    def test_other_school_manager_gets_404_everywhere(self):
        for path in PATHS:
            self.assertEqual(self.outsider.get(self.base + path).status_code, 404, path)
        for path in ("/school/me", "/school/duties", "/school/duties/preview", f"/school/members/{self.m5.key}/manager",
                     f"/members/{self.m5.key}/out"):
            self.assertEqual(self.outsider.post(self.base + path, {"paste": TSV, "on": "1"}).status_code, 404, path)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.is_manager)
        self.assertFalse(Duty.objects.exists())

    def test_key_of_other_school_through_own_token_404(self):
        for client, base, key in ((self.outsider, self.other_base, self.m5.key), (self.manager, self.base, self.other_member.key)):
            url = f"{base}/school/members/{key}/manager"
            self.assertEqual(client.post(url, {"on": "1"}).status_code, 404)
            self.assertEqual(client.post(f"{base}/members/{key}/out").status_code, 404)
        self.assertEqual(Member.objects.filter(pk__in=[self.m5.pk, self.other_member.pk], is_manager=True).count(), 1)

    def test_duties_replace_only_my_school(self):
        self.manager.post(f"{self.base}/school/duties", {"paste": TSV})
        self.outsider.post(f"{self.other_base}/school/duties", {"paste": "부서\t업무\t담당\n다른부\t다른일\t다른이"})
        self.assertEqual(Duty.objects.in_space(self.hub).count(), 3)
        self.assertEqual(Duty.objects.in_space(self.other).count(), 1)
        self.assertNotContains(self.manager.get(f"{self.base}/school?tab=duty"), "다른부")
        self.assertNotContains(self.outsider.get(f"{self.other_base}/school?tab=duty"), "교무기획부")

    def test_roster_and_logs_do_not_cross(self):
        self.teacher.post(f"{self.base}/school/me", {"group": "office", "role": "교무"})
        page = self.outsider.get(f"{self.other_base}/school")
        self.assertNotContains(page, "김도윤")
        self.assertNotContains(page, "교무실 · 교무")

    def test_duty_names_paired_only_with_own_roster(self):
        self.outsider.post(f"{self.other_base}/school/duties", {"paste": "부서\t업무\t담당\n부\t일\t김도윤"})
        self.assertContains(self.outsider.get(f"{self.other_base}/school?tab=duty"), "명부에 없음")

    def test_anonymous_never_sees_or_writes(self):
        anon = Client()
        for path in PATHS:
            self.assertNotEqual(anon.get(self.base + path).status_code, 200, path)
        self.assertNotEqual(anon.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 200)
        self.assertFalse(Duty.objects.exists())

    def test_old_grade_token_write_does_nothing(self):
        old = f"/s/{self.g5.token}"
        self.teacher.post(f"{old}/school/me", {"group": "office", "role": "교무"})
        self.manager.post(f"/s/{self.g4.token}/school/duties", {"paste": TSV})
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.group, "g5")
        self.assertFalse(Duty.objects.exists())
        self.assertEqual(self.outsider.get(f"{old}/school").status_code, 404)


class PlanBoundary(Base):
    def writes(self, client):
        return [client.post(f"{self.base}/school/me", {"group": "office", "role": "교무"}).status_code,
                client.post(f"{self.base}/school/duties", {"paste": TSV}).status_code,
                client.post(f"{self.base}/school/duties/preview", {"paste": TSV}).status_code,
                client.post(f"{self.base}/school/members/{self.m5.key}/manager", {"on": "1"}).status_code]

    def test_plan_free_is_read_only_like_expired(self):
        School.objects.filter(pk=self.school.pk).update(plan=School.Plan.FREE)
        self.assertEqual(self.writes(self.manager), [403, 403, 403, 403])
        for path in ("/school", "/school?tab=duty", "/school/me", "/school/duties"):
            self.assertEqual(self.manager.get(self.base + path).status_code, 200, path)
        self.assertFalse(Duty.objects.exists())

    def test_last_paid_day_still_writes_next_day_not(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate())
        self.assertEqual(self.manager.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 302)
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.manager.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 403)

    def test_expired_screens_offer_no_write_controls(self):
        self.expire()
        self.assertNotContains(self.manager.get(f"{self.base}/school/me"), 'id="schoolForm"')
        self.assertNotContains(self.manager.get(f"{self.base}/school/duties"), 'id="dutyPaste"')
        self.assertNotContains(self.manager.get(f"{self.base}/school?tab=duty"), "업무분장 붙여넣기")


class ManagerBoundary(Base):
    def toggle(self, client, m, on, **kw):
        return client.post(f"{self.base}/school/members/{m.key}/manager", {"on": "1" if on else "0"}, **kw)

    def managers(self):
        return Member.objects.in_space(self.hub).filter(is_manager=True).count()

    def test_two_managers_cannot_release_down_to_zero(self):
        self.toggle(self.manager, self.m5, True)
        self.assertEqual(self.toggle(self.teacher, self.m4, False).status_code, 302)
        self.assertEqual(self.toggle(self.teacher, self.m5, False).status_code, 400)
        self.assertEqual(self.managers(), 1)

    def test_self_release_loses_power_at_once(self):
        self.toggle(self.manager, self.m5, True)
        r = self.toggle(self.manager, self.m4, False, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "/manager")
        self.assertNotContains(r, self.hub.invite_token)
        self.assertEqual(self.toggle(self.manager, self.m4b, True).status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 403)

    def test_repeat_is_no_op_without_log(self):
        self.toggle(self.manager, self.m5, True)
        self.toggle(self.manager, self.m5, True)
        self.assertEqual(MemberLog.objects.in_space(self.hub).count(), 1)

    def test_get_not_allowed_and_csrf_enforced(self):
        self.assertEqual(self.manager.get(f"{self.base}/school/members/{self.m5.key}/manager").status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.u4)
        self.assertEqual(self.toggle(strict, self.m5, True).status_code, 403)
        self.assertEqual(strict.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 403)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.is_manager)

    def test_guest_manager_candidate_after_linking_google_ok_guest_cannot_act(self):
        guest_user = new_guest("손님")
        guest = Member.objects.create(space=self.hub, user=guest_user, name="손님", cls="4-3", group="g4")
        c = Client()
        c.force_login(guest_user)
        self.assertEqual(c.get(f"{self.base}/school").status_code, 200)
        self.assertEqual(self.toggle(c, guest, True).status_code, 403)
        self.assertEqual(c.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 403)
        page = self.manager.get(f"{self.base}/school")
        self.assertNotContains(page, f"/school/members/{guest.key}/manager")

    def test_every_position_word_gives_no_power(self):
        for role in ("교감", "교장", "교무부장", "실장", "관리자", "admin"):
            r = self.teacher.post(f"{self.base}/school/me", {"group": "office", "role_custom": role})
            self.assertEqual(r.status_code, 302, role)
            self.assertEqual(self.teacher.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 403, role)
            self.assertEqual(self.toggle(self.teacher, self.m4b, True).status_code, 403, role)
            self.assertEqual(self.teacher.post(f"{self.base}/invite").status_code, 403, role)
            self.assertNotContains(self.teacher.get(f"{self.base}/school"), self.hub.invite_token)
        self.assertEqual(self.managers(), 1)


class EditMeBoundary(Base):
    def test_get_with_query_changes_nothing(self):
        self.teacher.get(f"{self.base}/school/me", {"group": "office", "role": "교장"})
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.group, "g5")
        self.assertFalse(MemberLog.objects.exists())

    def test_only_own_seat_changes(self):
        self.teacher.post(f"{self.base}/school/me", {"group": "office", "role": "교무", "member": self.m4.key, "key": self.m4.key})
        self.m4.refresh_from_db()
        self.assertEqual(self.m4.group, "g4")

    def test_longest_custom_role_fits_log_columns(self):
        """운영 DB(PostgreSQL)는 길이를 넘기면 500이다. sqlite는 안 잡으니 길이를 직접 본다."""
        self.teacher.post(f"{self.base}/school/me", {"group": "support", "role_custom": "가" * 200})
        self.teacher.post(f"{self.base}/school/me", {"group": "support", "role_custom": "나" * 200})
        self.m5.refresh_from_db()
        self.assertLessEqual(len(self.m5.position), Member._meta.get_field("position").max_length)
        self.assertLessEqual(len(self.m5.cls), Member._meta.get_field("cls").max_length)
        for log in MemberLog.objects.in_space(self.hub):
            self.assertLessEqual(len(log.before), MemberLog._meta.get_field("before").max_length)
            self.assertLessEqual(len(log.after), MemberLog._meta.get_field("after").max_length)

    def test_blank_custom_role_is_not_a_role(self):
        r = self.teacher.post(f"{self.base}/school/me", {"group": "office", "role_custom": "  ​ "})
        self.assertEqual(r.status_code, 400)

    def test_log_keeps_latest_twenty_newest_first(self):
        for i in range(25):
            self.teacher.post(f"{self.base}/school/me", {"group": "support", "role_custom": f"직책{i}"})
        logs = self.manager.get(f"{self.base}/school").context["logs"]
        self.assertEqual(len(logs), 20)
        self.assertEqual(logs[0].after, "기타/지원 · 직책24")

    def test_log_row_shown_in_table_with_before_after(self):
        self.teacher.post(f"{self.base}/school/me", {"group": "office", "role": "교무"})
        page = self.manager.get(f"{self.base}/school")
        self.assertContains(page, "<td>5학년 1반</td>")
        self.assertContains(page, "<td>교무실 · 교무</td>")


class DutyCards(Base):
    def apply(self, paste):
        return self.manager.post(f"{self.base}/school/duties", {"paste": paste})

    def test_people_count_counts_people_not_cells(self):
        self.apply("부서\t업무\t담당\n연구부\t연수\t김도윤, 송민재")
        card = _dept_cards(self.hub, self.m5)[0]
        self.assertEqual(card["people"], 2)

    def test_same_name_in_two_groups_shows_both(self):
        self.member("twin", "김도윤", "교무실", "office", "교무")
        self.apply("부서\t업무\t담당\n연구부\t연수\t김도윤")
        self.assertContains(self.teacher.get(f"{self.base}/school?tab=duty"), "5학년 · 교무실")

    def test_repeated_header_row_is_not_a_task(self):
        d = read_duties("부서\t업무\t담당\n연구부\t연수\t가\n부서\t업무\t담당\n교무부\t학적\t나")
        self.assertEqual([r["dept"] for r in d["rows"]], ["연구부", "교무부"])

    def test_empty_who_has_no_warning(self):
        self.apply("부서\t업무\t담당\n연구부\t연수\t")
        self.assertNotContains(self.teacher.get(f"{self.base}/school?tab=duty"), "명부에 없음")

    def test_bad_previews_never_500(self):
        url = f"{self.base}/school/duties/preview"
        for data in ({}, {"paste": ""}, {"html": "<table></table>"}, {"html": "<table><tr></tr></table>"},
                     {"html": "<table><tr><td>혼자</td></tr></table>"}, {"html": "<" * 1000},
                     {"html": "<table>" + "<tr><td>x</td></tr>" * 2000 + "</table>"},
                     {"html": "<table><tr><td>가</td></tr></table>" + "x" * 1_000_001}):
            r = self.manager.post(url, data)
            self.assertEqual(r.status_code, 200, str(data)[:40])
            self.assertContains(r, "field-err")
            self.assertNotContains(r, "이대로 바꾸기")

    def test_apply_empty_keeps_old(self):
        self.apply(TSV)
        self.assertEqual(self.apply("").status_code, 400)
        self.assertEqual(Duty.objects.in_space(self.hub).count(), 3)

    def test_second_paste_with_fewer_rows_leaves_no_leftover(self):
        self.apply(TSV)
        self.apply("부서\t업무\t담당\n새부서\t새일\t")
        self.assertEqual(list(Duty.objects.in_space(self.hub).values_list("dept", flat=True)), ["새부서"])

    def test_textarea_breakout_escaped(self):
        evil = "</textarea><script>alert(1)</script>"
        html = table(HEAD, f"<tr><td>부</td><td>{evil.replace('<', '&lt;').replace('>', '&gt;')}</td><td>가</td></tr>")
        r = self.manager.post(f"{self.base}/school/duties/preview", {"html": html})
        self.assertNotContains(r, "<script>alert")
        self.assertNotContains(r, "</textarea><script")
        bad = self.manager.post(f"{self.base}/school/duties", {"paste": evil})
        self.assertEqual(bad.status_code, 400)
        self.assertNotContains(bad, "<script>alert", status_code=400)

    def test_toast_with_evil_name_escaped(self):
        Member.objects.filter(pk=self.m5.pk).update(name='"><script>alert(1)</script>')
        r = self.manager.post(f"{self.base}/school/members/{self.m5.key}/manager", {"on": "1"}, headers={"HX-Request": "true"})
        self.assertNotContains(r, "<script>alert")


class Scale(Base):
    def fill(self, n, start=0):
        for i in range(start, start + n):
            g = i % 10
            group = Member.Group.values[g]
            if g < 6:
                Member.objects.create(space=self.hub, user=google(f"s{i}", f"교사{i}"), name=f"교사{i}", cls=f"{g + 1}-{i % 5 + 1}",
                                      group=group, position=f"{i % 5 + 1}반")
            else:
                Member.objects.create(space=self.hub, user=google(f"s{i}", f"직원{i}"), name=f"직원{i}", cls=Member.Group(group).label,
                                      group=group, position="주무관")

    def test_forty_people_all_in_cards(self):
        self.fill(37)
        page = self.manager.get(f"{self.base}/school")
        self.assertEqual(sum(g["n"] for g in page.context["group_cards"]), 40)
        self.assertContains(page, '<b class="num">40</b>')

    def test_queries_do_not_grow_with_people(self):
        self.fill(37)
        self.manager.post(f"{self.base}/school/duties", {"paste": TSV})
        counts = []
        for path in ("/school", "/school?tab=duty"):
            with CaptureQueriesContext(connection) as q:
                self.manager.get(self.base + path)
            counts.append(len(q))
        self.fill(40, start=100)
        for path, before in zip(("/school", "/school?tab=duty"), counts):
            with CaptureQueriesContext(connection) as q:
                self.manager.get(self.base + path)
            self.assertLessEqual(len(q), before + 2, path)
