"""H3 학교 화면: 학년분장·그룹 카드, 내 그룹·직책 고치기와 이력, 관리자 지정, 업무분장 붙여넣기, 빈 학교, 옛 명단 화면 넘기기."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase
from django.utils import timezone

from accounts.models import new_guest

from .duty_paste import PasteError, read_duties
from .merge import open_school
from .models import Duty, Member, MemberLog, School, Space
from .school import _dept_cards
from .test_merge import UNTIL, MergeBase

User = get_user_model()
TSV = "부서\t업무\t담당\n교무기획부\t학사일정\t송민재\n교무기획부\t학적\t김도윤\n행정실\t시설\t없는사람"
HWP = ("<html><body><table><tr><td colspan='4'>2027 업무분장</td></tr>"
       "<tr><td><p>부 서</p></td><td>담당</td><td>업무</td><td>비고</td></tr>"
       "<tr><td rowspan='2'>연구부</td><td>송민재</td><td>수업공개</td><td>메모</td></tr>"
       "<tr><td>김도윤</td><td>연수</td><td>메모</td></tr></table></body></html>")


def google(sub, name):
    return User.objects.create(username=f"google:{sub}", first_name=name)


class ReadDutiesTests(SimpleTestCase):
    def test_tsv(self):
        d = read_duties(TSV)
        self.assertEqual(d["rows"][0], {"dept": "교무기획부", "task": "학사일정", "who": "송민재"})
        self.assertEqual(len(d["rows"]), 3)

    def test_blank_dept_follows_previous_row(self):
        d = read_duties("부서\t업무\t담당\n연구부\t연수\t가\n\t평가\t나")
        self.assertEqual(d["rows"][1]["dept"], "연구부")

    def test_columns_found_by_name_other_columns_dropped(self):
        d = read_duties("담당자\t전화\t담당 업무\t부서명\n가\t010-1\t연수\t연구부")
        self.assertEqual(d["rows"], [{"dept": "연구부", "task": "연수", "who": "가"}])
        self.assertEqual(d["dropped"], 1)

    def test_title_line_above_header(self):
        d = read_duties("2027 업무분장\t\t\n부서\t업무\t담당\n연구부\t연수\t가")
        self.assertEqual(len(d["rows"]), 1)

    def test_no_header_three_columns_in_order(self):
        d = read_duties("연구부\t연수\t가\n연구부\t평가\t나")
        self.assertEqual(len(d["rows"]), 2)

    def test_blank_who_kept_missing_task_counted(self):
        d = read_duties("부서\t업무\t담당\n연구부\t연수\t\n연구부\t\t가\n합계\t\t")
        self.assertEqual(d["rows"], [{"dept": "연구부", "task": "연수", "who": ""}])
        self.assertEqual(d["skipped"], 1)

    def test_errors(self):
        for text in ("", "부서\n업무\n담당", "이름\t전화\t메모\t비고\n가\t1\t2\t3", "부서\t담당\n연구부\t가", "부서\t업무\t담당"):
            with self.assertRaises(PasteError, msg=text):
                read_duties(text)
        with self.assertRaises(PasteError):
            read_duties("부서\t업무\t담당\n" + "연구부\t연수\t가\n" * 501)


class Base(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)
        self.base = f"/s/{self.hub.token}"
        self.m4.refresh_from_db()
        self.m5.refresh_from_db()
        self.m4b.refresh_from_db()
        self.manager = Client()
        self.manager.force_login(self.u4)
        self.teacher = Client()
        self.teacher.force_login(self.u5)

    def expire(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))

    def member(self, sub, name, cls, group, position=""):
        return Member.objects.create(space=self.hub, user=google(sub, name), name=name, cls=cls, group=group, position=position)


class GroupCardTests(Base):
    def test_cards_for_every_group_with_counts_and_order(self):
        self.member("a", "교무교사", "교무실", "office", "교무")
        self.member("b", "교장선생", "교무실", "office", "교장")
        self.member("c", "교감선생", "교무실", "office", "교감")
        self.member("d", "다섯번째", "4-전담", "g4", "전담")
        page = self.teacher.get(f"{self.base}/school")
        self.assertContains(page, "학년분장")
        self.assertContains(page, "업무분장")
        # 사람이 있는 그룹만 카드, 없는 그룹은 이름만 한 줄(33번 3)
        cards = {c["label"]: c for c in page.context["group_cards"]}
        self.assertEqual(len(cards) + len(page.context["empty_groups"]), 10)
        self.assertContains(page, "아직 없는 소속:")
        office = [r["m"].name for r in cards["교무실"]["rows"]]
        self.assertEqual(office, ["교장선생", "교감선생", "교무교사"])
        g4 = cards["4학년"]
        self.assertEqual([r["pos"] for r in g4["rows"]], ["1반", "2반", "전담"])
        self.assertEqual(g4["n"], 3)

    def test_my_row_highlighted_and_same_for_all_teachers(self):
        page = self.teacher.get(f"{self.base}/school")
        self.assertContains(page, 'class="who mine"', count=1)
        self.assertContains(page, "(나)", count=1)
        self.assertContains(page, "송민재")
        self.assertContains(page, "내 소속·직책 고치기")

    def test_school_menu_only_no_duplicate_teacher_menu(self):
        page = self.teacher.get(f"{self.base}/school")
        self.assertNotContains(page, f'<a href="{self.base}/"')

    def test_grade_space_and_other_school_404(self):
        c = Client()
        c.force_login(self.far_user)
        far = f"/s/{self.far.token}"
        duty = Duty.objects.create(space=self.hub, dept="부", task="일", who="사람", order=0)
        for path in ("/school", "/school/me", "/school/duties"):
            self.assertEqual(c.get(far + path).status_code, 404, path)
            self.assertEqual(c.get(self.base + path).status_code, 404, path)
        for path in ("/school/me", "/school/duties", "/school/duties/preview", f"/school/members/{self.far_member.key}/manager"):
            self.assertEqual(c.post(far + path, {"paste": TSV}).status_code, 404, path)
        self.assertEqual(self.manager.post(f"{self.base}/school/members/{self.far_member.key}/manager", {"on": "1"}).status_code, 404)
        self.assertEqual(Duty.objects.in_space(self.far).count(), 0)
        self.assertTrue(Duty.objects.filter(pk=duty.pk).exists())


class EditMeTests(Base):
    def post(self, **data):
        return self.teacher.post(f"{self.base}/school/me", data)

    def test_form_opens_with_current_values(self):
        page = self.teacher.get(f"{self.base}/school/me")
        self.assertContains(page, 'name="group" value="g5"')
        self.assertContains(page, 'name="cls" value="5-1"')
        self.assertNotContains(page, 'name="name"')

    def test_group_switch_returns_form_only(self):
        r = self.teacher.get(f"{self.base}/school/me", {"group": "office"}, headers={"HX-Request": "true"})
        self.assertContains(r, 'id="schoolForm"')
        self.assertContains(r, "교무실무사")
        self.assertNotContains(r, "<html")

    def test_grade_to_office_keeps_cls_group_position_consistent(self):
        r = self.post(group="office", role="교무실무사")
        self.assertEqual(r.status_code, 302)
        self.m5.refresh_from_db()
        self.assertEqual((self.m5.group, self.m5.cls, self.m5.position), ("office", "교무실", "교무실무사"))
        self.assertFalse(self.m5.in_grade)

    def test_office_to_grade(self):
        self.post(group="office", role="교무")
        self.post(group="g4", cls="4-3")
        self.m5.refresh_from_db()
        self.assertEqual((self.m5.group, self.m5.cls, self.m5.position), ("g4", "4-3", "3반"))

    def test_custom_role_cleaned(self):
        self.post(group="support", role_custom="늘봄‮실무사")
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.position, "늘봄실무사")

    def test_validation_same_as_join(self):
        before = (self.m5.group, self.m5.cls, self.m5.position)
        for data in ({"group": "nope"}, {"group": "g4", "cls": "5-1"}, {"group": "g4"}, {"group": "office"}, {"group": "office", "role": "없는직책"}):
            r = self.post(**data)
            self.assertEqual(r.status_code, 400, data)
        self.m5.refresh_from_db()
        self.assertEqual((self.m5.group, self.m5.cls, self.m5.position), before)
        self.assertFalse(MemberLog.objects.in_space(self.hub).exists())

    def test_every_edit_logged_who_when_what(self):
        before = timezone.now()
        self.post(group="office", role="교무")
        log = MemberLog.objects.in_space(self.hub).get()
        self.assertEqual((log.by, log.name, log.before, log.after), ("김도윤", "김도윤", "5학년 1반", "교무실 · 교무"))
        self.assertGreaterEqual(log.at, before)

    def test_no_change_no_log(self):
        self.post(group="g5", cls="5-1")
        self.assertFalse(MemberLog.objects.in_space(self.hub).exists())

    def test_settings_class_change_also_logged(self):
        self.teacher.post(f"{self.base}/settings/me", {"cls": "2-전담"})
        log = MemberLog.objects.in_space(self.hub).get()
        self.assertEqual((log.before, log.after), ("5학년 1반", "2학년 전담"))

    def test_settings_name_change_logged_in_school_and_grade_space_never(self):
        """H4 보안 검토: 이름은 담당 짝짓기·대신 내기 이름 찾기에 쓰이므로 학교 공간에서는 이력에 남긴다."""
        self.teacher.post(f"{self.base}/settings/me", {"name": "새이름"})
        log = MemberLog.objects.get()
        self.assertEqual((log.name, log.after), ("새이름", "새이름"))
        self.teacher.post(f"{self.base}/settings/me", {"name": "새이름"})
        self.assertEqual(MemberLog.objects.count(), 1)  # 안 바뀌면 남기지 않는다
        c = Client()
        c.force_login(self.far_user)
        c.post(f"/s/{self.far.token}/settings/me", {"name": "먼이름", "cls": "4-2"})
        self.assertEqual(MemberLog.objects.count(), 1)

    def test_log_only_managers_see(self):
        self.post(group="office", role="교무")
        self.assertContains(self.manager.get(f"{self.base}/school"), "교무실 · 교무")
        self.assertContains(self.manager.get(f"{self.base}/school"), "최근 고친 기록")
        other = Client()
        other.force_login(self.u4b)
        self.assertNotContains(other.get(f"{self.base}/school"), "최근 고친 기록")

    def test_logs_stay_in_their_space(self):
        MemberLog.objects.create(space=self.far, by="남", name="남", before="먼학교값", after="먼학교값2")
        self.assertNotContains(self.manager.get(f"{self.base}/school"), "먼학교값")

    def test_settings_links_to_edit(self):
        self.assertContains(self.teacher.get(f"{self.base}/settings"), f'href="{self.base}/school/me"')
        self.post(group="office", role="교무")
        self.assertContains(self.teacher.get(f"{self.base}/settings"), f'href="{self.base}/school/me"')

    def test_expired_read_ok_write_403(self):
        self.expire()
        self.assertEqual(self.teacher.get(f"{self.base}/school/me").status_code, 200)
        self.assertEqual(self.post(group="office", role="교무").status_code, 403)
        self.assertNotContains(self.teacher.get(f"{self.base}/school"), "내 소속·직책 고치기")


class ManagerTests(Base):
    def toggle(self, client, m, on):
        return client.post(f"{self.base}/school/members/{m.key}/manager", {"on": "1" if on else "0"})

    def test_designate_and_release_logged(self):
        self.assertEqual(self.toggle(self.manager, self.m5, True).status_code, 302)
        self.m5.refresh_from_db()
        self.assertTrue(self.m5.is_manager)
        log = MemberLog.objects.in_space(self.hub).get()
        self.assertEqual((log.by, log.name, log.after), ("송민재", "김도윤", "관리자"))
        self.assertEqual(self.toggle(self.manager, self.m5, False).status_code, 302)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.is_manager)
        self.assertEqual(MemberLog.objects.in_space(self.hub).count(), 2)

    def test_guest_refused(self):
        guest = Member.objects.create(space=self.hub, user=new_guest("손님"), name="손님", cls="4-3", group="g4")
        self.assertEqual(self.toggle(self.manager, guest, True).status_code, 400)
        guest.refresh_from_db()
        self.assertFalse(guest.is_manager)
        self.assertFalse(MemberLog.objects.exists())
        self.assertContains(self.manager.get(f"{self.base}/school"), "구글 계정을 이으면")

    def test_seat_without_user_refused(self):
        empty = Member.objects.create(space=self.hub, name="빈자리", cls="4-3", group="g4")
        self.assertEqual(self.toggle(self.manager, empty, True).status_code, 400)

    def test_last_manager_release_refused(self):
        r = self.manager.post(f"{self.base}/school/members/{self.m4.key}/manager", {"on": "0"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "마지막 관리자", status_code=400)
        self.m4.refresh_from_db()
        self.assertTrue(self.m4.is_manager)
        self.assertFalse(MemberLog.objects.exists())

    def test_htmx_returns_whole_school_body_with_toast(self):
        r = self.manager.post(f"{self.base}/school/members/{self.m5.key}/manager", {"on": "1"}, headers={"HX-Request": "true"})
        self.assertContains(r, 'id="schoolMain"')
        self.assertContains(r, "data-toast-now")
        self.assertNotContains(r, "<html")

    def test_not_manager_403_no_buttons(self):
        self.assertEqual(self.toggle(self.teacher, self.m4b, True).status_code, 403)
        page = self.teacher.get(f"{self.base}/school")
        self.assertNotContains(page, "/manager")
        self.assertNotContains(page, "/out")

    def test_position_word_gives_no_power(self):
        self.teacher.post(f"{self.base}/school/me", {"group": "office", "role": "교감"})
        self.teacher.post(f"{self.base}/school/me", {"group": "office", "role_custom": "관리자"})
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.position, "관리자")
        self.assertFalse(self.m5.is_manager)
        page = self.teacher.get(f"{self.base}/school")
        self.assertNotContains(page, "/manager")
        self.assertNotContains(page, "/school/duties")
        self.assertEqual(self.toggle(self.teacher, self.m4b, True).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/school/duties", {"paste": TSV}).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/members/{self.m4b.key}/out").status_code, 403)

    def test_expired_write_403(self):
        self.expire()
        self.assertEqual(self.toggle(self.manager, self.m5, True).status_code, 403)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.is_manager)
        self.assertNotContains(self.manager.get(f"{self.base}/school"), "/manager")

    def test_manager_can_remove_and_gets_school_body(self):
        r = self.manager.post(f"{self.base}/members/{self.m4b.key}/out", headers={"HX-Request": "true"})
        self.assertContains(r, 'id="schoolMain"')
        self.assertContains(r, "내보냈어요")
        self.assertFalse(Member.objects.filter(pk=self.m4b.pk).exists())
        self.assertNotContains(r, 'id="roster"')

    def test_row_dialog_for_manager(self):
        page = self.manager.get(f"{self.base}/school")
        self.assertContains(page, f"/members/{self.m5.key}/out")
        self.assertContains(page, f"/school/members/{self.m5.key}/manager")
        self.assertNotContains(page, f"/members/{self.m4.key}/out")  # 나는 못 내보냄
        self.assertContains(page, "마지막 관리자라")  # 나 하나뿐이면 해제 단추 대신 안내


class DutyPasteTests(Base):
    def apply(self, client=None, paste=TSV):
        return (client or self.manager).post(f"{self.base}/school/duties", {"paste": paste})

    def test_replaces_whole_table_and_pairs_with_roster(self):
        Duty.objects.create(space=self.hub, dept="옛부서", task="옛업무", who="옛사람", order=0)
        r = self.apply()
        self.assertRedirects(r, f"{self.base}/school?tab=duty", fetch_redirect_response=False)
        self.assertEqual(list(Duty.objects.in_space(self.hub).order_by("order").values_list("dept", "task", "who", "order")),
                         [("교무기획부", "학사일정", "송민재", 0), ("교무기획부", "학적", "김도윤", 1), ("행정실", "시설", "없는사람", 2)])
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        self.assertContains(page, "교무기획부")
        self.assertContains(page, "4학년")  # 송민재의 그룹
        self.assertContains(page, "명부에 없음", count=1)
        self.assertNotContains(page, "옛부서")
        depts = page.context["dept_cards"]
        self.assertEqual([(d["dept"], d["people"], len(d["rows"])) for d in depts], [("교무기획부", 2, 2), ("행정실", 1, 1)])

    def test_who_is_text_not_fk_follows_roster_changes(self):
        self.apply()
        Member.objects.filter(pk=self.m5.pk).update(group="office", cls="교무실", position="교무")
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        self.assertContains(page, "교무실")
        Member.objects.filter(pk=self.m4b.pk).delete()
        self.assertEqual(Duty.objects.in_space(self.hub).filter(who="김도윤").count(), 1)

    def test_my_row_highlighted_multi_name_cell(self):
        self.apply(paste="부서\t업무\t담당\n연구부\t연수\t김도윤, 송민재")
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        self.assertContains(page, 'class="task mine"', count=1)
        self.assertNotContains(page, "명부에 없음")

    def test_preview_changes_nothing_html_table_to_tsv(self):
        Duty.objects.create(space=self.hub, dept="옛부서", task="옛업무", who="옛사람", order=0)
        r = self.manager.post(f"{self.base}/school/duties/preview", {"html": HWP})
        self.assertContains(r, 'id="tsvOut"')
        self.assertContains(r, "연구부")
        self.assertContains(r, "수업공개")
        self.assertContains(r, "지금 업무분장 1개")
        self.assertContains(r, "저장하지 않아요")  # 비고 칸
        self.assertEqual(Duty.objects.in_space(self.hub).get().dept, "옛부서")
        tsv = r.context["tsv"]
        self.apply(paste=tsv)
        self.assertEqual(list(Duty.objects.in_space(self.hub).order_by("order").values_list("dept", "task", "who")),
                         [("연구부", "수업공개", "송민재"), ("연구부", "연수", "김도윤")])

    def test_preview_flags_unknown_names(self):
        r = self.manager.post(f"{self.base}/school/duties/preview", {"paste": TSV})
        self.assertContains(r, "없는사람")
        self.assertContains(r, "명부에 없는 이름 1명")

    def test_preview_error_and_html_without_table(self):
        r = self.manager.post(f"{self.base}/school/duties/preview", {"html": "<p>표 아님</p>"})
        self.assertContains(r, "표를 찾지 못했어요")
        self.assertNotContains(r, "이대로 바꾸기")

    def test_bad_apply_keeps_old(self):
        Duty.objects.create(space=self.hub, dept="옛부서", task="옛업무", who="옛사람", order=0)
        r = self.apply(paste="칸구분없음")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Duty.objects.in_space(self.hub).count(), 1)

    def test_not_manager_403(self):
        self.assertEqual(self.apply(self.teacher).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/school/duties/preview", {"paste": TSV}).status_code, 403)
        self.assertEqual(self.teacher.get(f"{self.base}/school/duties").status_code, 403)
        self.assertFalse(Duty.objects.exists())

    def test_expired_write_403_read_200(self):
        self.apply()
        self.expire()
        self.assertEqual(self.apply(paste="부서\t업무\t담당\n새\t새\t새").status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/school/duties/preview", {"paste": TSV}).status_code, 403)
        self.assertEqual(Duty.objects.in_space(self.hub).count(), 3)
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "교무기획부")
        manager_page = self.manager.get(f"{self.base}/school?tab=duty")
        self.assertNotContains(manager_page, "/school/duties")
        self.assertEqual(self.manager.get(f"{self.base}/school/duties").status_code, 200)

    def test_other_school_duties_not_shown_or_removed(self):
        Duty.objects.create(space=self.far, dept="먼학교부서", task="일", who="사람", order=0)
        self.apply()
        self.assertTrue(Duty.objects.filter(space=self.far).exists())
        self.assertNotContains(self.manager.get(f"{self.base}/school?tab=duty"), "먼학교부서")


class PairTests(Base):
    """담당 칸: 쉼표·/·줄바꿈으로만 나누고, 괄호를 떼고 공백을 뺀 이름을 명부와 비교, 사람별 경고."""

    def cell(self, who):
        Duty.objects.in_space(self.hub).delete()
        Duty.objects.create(space=self.hub, dept="부", task="일", who=who, order=0)
        return _dept_cards(self.hub, self.m5)[0]["rows"][0]

    def test_spaced_and_parenthesis_names_match(self):
        for who in ("김 도 윤", "김도윤(부장)", "김도윤 (부장)", "김도윤（부장）"):
            r = self.cell(who)
            self.assertEqual((r["people"], r["groups"], r["unknown"], r["me"]), (["김도윤"], "5학년", [], True), who)

    def test_each_person_judged(self):
        r = self.cell("김도윤, 전입예정/송민재")
        self.assertEqual(r["people"], ["김도윤", "전입예정", "송민재"])
        self.assertEqual(r["unknown"], ["전입예정"])
        self.assertEqual(r["groups"], "5학년 · 4학년")
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        self.assertContains(page, "전입예정 명부에 없음")

    def test_space_splits_only_when_every_piece_is_on_roster(self):
        """H4 리드 결정: 조각이 모두 명부 이름이면 두 사람, 아니면 한 사람('김 도 윤'·'부장 김도윤')."""
        self.assertEqual(self.cell("김도윤 송민재")["people"], ["김도윤", "송민재"])
        self.assertEqual(self.cell("김 도 윤")["people"], ["김도윤"])
        self.assertEqual(self.cell("부장 김도윤")["people"], ["부장김도윤"])

    def test_vacant_is_not_a_person(self):
        r = self.cell("(공석)")
        self.assertEqual((r["people"], r["unknown"]), ([], []))

    def test_preview_warns_per_person(self):
        r = self.manager.post(f"{self.base}/school/duties/preview", {"paste": "부서\t업무\t담당\n연구부\t연수\t김도윤, 전입예정"})
        self.assertContains(r, "명부에 없는 이름 1명: 전입예정")


class ScreenDetailTests(Base):
    def test_tab_counts(self):
        self.assertContains(self.teacher.get(f"{self.base}/school"), "소속 10개 · 교직원 3명")
        self.manager.post(f"{self.base}/school/duties", {"paste": TSV})
        self.assertContains(self.teacher.get(f"{self.base}/school?tab=duty"), "부서 2개 · 업무 3개")

    def test_edit_me_toast_once_only_when_changed(self):
        self.teacher.post(f"{self.base}/school/me", {"group": "g5", "cls": "5-1"})
        self.assertNotContains(self.teacher.get(f"{self.base}/school"), "data-toast-now")
        page = self.teacher.post(f"{self.base}/school/me", {"group": "office", "role": "교무"}, follow=True)
        self.assertContains(page, 'data-toast-now="바꿨어요: 교무실 · 교무"')
        self.assertNotContains(self.teacher.get(f"{self.base}/school"), "data-toast-now")

    def test_apply_button_above_preview_and_below_long_table(self):
        one = self.manager.post(f"{self.base}/school/duties/preview", {"paste": TSV}).content.decode()
        self.assertEqual(one.count("이대로 바꾸기"), 1)
        self.assertLess(one.index("이대로 바꾸기"), one.index("<table"))
        long = "부서\t업무\t담당\n" + "".join(f"연구부\t일{i}\t가\n" for i in range(11))
        self.assertContains(self.manager.post(f"{self.base}/school/duties/preview", {"paste": long}), "이대로 바꾸기", count=2)

    def test_too_long_values_cut_to_model_length(self):
        d = read_duties(f"부서\t업무\t담당\n{'가' * 500}\t{'나' * 500}\t{'다' * 500}")
        self.assertEqual([len(d["rows"][0][k]) for k in ("dept", "task", "who")], [100, 200, 100])


class XssTests(Base):
    EVIL = '<script>alert(1)</script><img src=x onerror=alert(2)>'

    def test_paste_and_names_escaped(self):
        tsv = f"부서\t업무\t담당\n{self.EVIL}\t{self.EVIL}\t{self.EVIL}"
        r = self.manager.post(f"{self.base}/school/duties/preview", {"paste": tsv})
        self.assertNotContains(r, "<script>alert")
        self.assertNotContains(r, "<img src=x")
        self.manager.post(f"{self.base}/school/duties", {"paste": tsv})
        r = self.manager.get(f"{self.base}/school?tab=duty")
        self.assertNotContains(r, "<script>alert")
        self.assertNotContains(r, "<img src=x")

    def test_html_table_cells_escaped(self):
        html = "<table><tr><td>부서</td><td>업무</td><td>담당</td></tr><tr><td>가</td><td>&lt;script&gt;alert(1)&lt;/script&gt;</td><td>나</td></tr></table>"
        r = self.manager.post(f"{self.base}/school/duties/preview", {"html": html})
        self.assertNotContains(r, "<script>alert")

    def test_member_name_and_position_escaped_in_cards_dialogs_and_log(self):
        Member.objects.filter(pk=self.m5.pk).update(name=self.EVIL[:40])
        self.teacher.post(f"{self.base}/school/me", {"group": "support", "role_custom": self.EVIL[:40]})
        r = self.manager.get(f"{self.base}/school")
        self.assertNotContains(r, "<script>alert")
        self.assertNotContains(r, "<img src=x")
        self.assertContains(r, "&lt;script&gt;")


class EmptySchoolTests(Base):
    def alone(self):
        Member.objects.in_space(self.hub).exclude(pk=self.m4.pk).delete()

    def test_only_manager_invite_guidance(self):
        self.alone()
        page = self.manager.get(f"{self.base}/school")
        self.assertContains(page, "초대 링크")
        self.assertContains(page, "data-open=\"#inviteDlg\"")
        self.assertContains(page, self.hub.invite_token)

    def test_many_members_no_empty_guidance(self):
        self.assertNotContains(self.manager.get(f"{self.base}/school"), "아직 혼자")

    def test_empty_duties_guidance_by_role(self):
        page = self.manager.get(f"{self.base}/school?tab=duty")
        self.assertContains(page, "업무분장표를 붙여넣으면")
        self.assertContains(page, f'href="{self.base}/school/duties"')
        page = self.teacher.get(f"{self.base}/school?tab=duty")
        self.assertContains(page, "관리자가 업무분장표를 넣으면")
        self.assertNotContains(page, f'href="{self.base}/school/duties"')


class OldRosterRedirectTests(Base):
    def test_school_space_root_goes_to_school(self):
        self.assertRedirects(self.teacher.get(f"{self.base}/"), f"{self.base}/school", fetch_redirect_response=False)

    def test_grade_space_root_stays(self):
        c = Client()
        c.force_login(self.far_user)
        self.assertEqual(c.get(f"/s/{self.far.token}/").status_code, 200)

    def test_school_page_does_not_poll(self):
        self.assertNotContains(self.manager.get(f"{self.base}/school"), "every 5s")

    def test_school_buttons_point_to_school(self):
        for path in ("/welcome", "/settings?a=space"):
            self.assertContains(self.teacher.get(self.base + path), f'href="{self.base}/school"')
        self.assertNotContains(self.teacher.get(f"{self.base}/welcome"), f'href="{self.base}/"')

    def test_grade_buttons_unchanged(self):
        c = Client()
        c.force_login(self.far_user)
        self.assertContains(c.get(f"/s/{self.far.token}/welcome"), f'href="/s/{self.far.token}/"')

    def test_manager_has_invite_on_school_page_and_reissue_works(self):
        page = self.manager.get(f"{self.base}/school")
        self.assertContains(page, self.hub.invite_token)
        self.assertContains(page, 'id="msg"')
        r = self.manager.post(f"{self.base}/invite", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        fresh = Space.objects.get(pk=self.hub.pk).invite_token
        self.assertNotEqual(fresh, self.hub.invite_token)
        self.assertContains(self.manager.get(f"{self.base}/school"), fresh)

    def test_teacher_has_no_invite_on_school_page(self):
        page = self.teacher.get(f"{self.base}/school")
        self.assertNotContains(page, self.hub.invite_token)
        self.assertNotContains(page, 'id="msg"')
