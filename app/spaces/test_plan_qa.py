"""O1 독립 검증(gt-qa): test_plan·test_bell·test_purge에 없는 경계만. 구글은 force_login으로 대체한다."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from collect import bell
from collect.models import Cell, Collection, Notification, Row
from hwpfiles import retention
from meals.models import MealSettings, PhotoLink
from notices.models import Notice
from repairs.models import VendorAccess, VendorAccount

from .models import Member, PlanRequest, School, Space
from .test_plan import FORM, ON, UNTIL, Base, google


def at9(days=0):
    return timezone.localtime().replace(hour=9, minute=0) + timedelta(days=days)


def grade_school(code="1", name="가람초"):
    return School.objects.create(neis_atpt="J10", neis_code=code, name=name)


class FlowEdges(Base):
    def request(self, school, user=None, **over):
        return PlanRequest.objects.create(school=school, user=user or self.user, **{"by_name": "이수빈", "position": "교무부장", "email": "a@b.kr", **over})

    def test_school_with_grades_and_seatless_applicant_merges_and_seats(self):
        s, other = grade_school(), grade_school("2", "다른초")
        g4 = Space.objects.create(school=s, grade=4, name="4학년")
        g5 = Space.objects.create(school=s, grade=5, name="5학년")
        Member.objects.create(space=g4, user=google("41"), name="김하나", cls="4-1")
        Member.objects.create(space=g5, user=google("51"), name="박다온", cls="5-1")
        untouched = Space.objects.create(school=other, grade=4, name="4학년")
        Member.objects.create(space=untouched, user=google("o"), name="다른사람", cls="4-1")
        pr = self.request(s)
        call_command("school_plan", "1", *ON, f"--manager-request={pr.pk}", "--identity-checked", stdout=StringIO())
        hub = Space.objects.get(school=s, kind="school")
        self.assertEqual(Member.objects.in_space(hub).count(), 3)
        self.assertEqual(list(Member.objects.in_space(hub).filter(is_manager=True).values_list("user", flat=True)), [self.user.pk])
        g4.refresh_from_db()
        self.assertEqual(g4.merged_into, hub)
        self.assertEqual(Member.objects.in_space(untouched).count(), 1)
        self.assertEqual(School.objects.get(pk=other.pk).plan, "free")
        self.assertIn(f"/s/{hub.token}", self.client.get(f"/s/{g4.token}/")["Location"])

    def test_no_manager_rolls_everything_back(self):
        s = grade_school()
        g4 = Space.objects.create(school=s, grade=4, name="4학년")
        Member.objects.create(space=g4, user=google("41"), name="김하나", cls="4-1")
        pr = self.request(s)
        with self.assertRaises(CommandError):
            call_command("school_plan", "1", *ON, stdout=StringIO())
        s.refresh_from_db()
        g4.refresh_from_db()
        pr.refresh_from_db()
        self.assertEqual((s.plan, s.paid_until, g4.merged_into, pr.status), ("free", None, None, "pending"))
        self.assertFalse(Space.objects.filter(school=s, kind="school").exists())

    def test_deleted_applicant_refused_and_rolled_back(self):
        s = grade_school()
        gone = google("gone")
        pr = self.request(s, user=gone)
        gone.delete()
        with self.assertRaises(CommandError):
            call_command("school_plan", "1", *ON, f"--manager-request={pr.pk}", stdout=StringIO())
        s.refresh_from_db()
        self.assertEqual(s.plan, "free")
        self.assertFalse(Space.objects.filter(school=s, kind="school").exists())

    def test_outputs_never_show_quote_key_and_on_shows_no_person(self):
        s = grade_school()
        pr = self.request(s)
        out = StringIO()
        call_command("school_plan", "requests", stdout=out)
        self.assertNotIn(pr.key, out.getvalue())
        self.assertTrue(out.getvalue().startswith("대기 1건"))
        out = StringIO()
        call_command("school_plan", "1", *ON, f"--manager-request={pr.pk}", stdout=out)
        for secret in ("이수빈", "a@b.kr", pr.key, Space.objects.get(school=s, kind="school").token):
            self.assertNotIn(secret, out.getvalue())
        out = StringIO()
        call_command("school_plan", "requests", stdout=out)
        self.assertEqual(out.getvalue().strip(), "대기 0건")

    def test_two_applicants_first_opens_second_closed_and_refused(self):
        s = grade_school()
        second = google("second")
        pr1, pr2 = self.request(s), self.request(s, user=second)
        call_command("school_plan", "1", *ON, f"--manager-request={pr1.pk}", stdout=StringIO())
        pr2.refresh_from_db()
        self.assertEqual(pr2.status, "done")
        other = Client()
        other.force_login(second)
        self.assertEqual(other.get(f"/school-plan/q/{pr2.key}").status_code, 200)
        self.assertEqual(self.client.get(f"/school-plan/q/{pr2.key}").status_code, 404)
        hit = {"atpt": s.neis_atpt, "code": s.neis_code, "name": s.name, "office": "", "address": ""}
        with mock.patch("spaces.neis.search_schools", return_value=([hit], 1)):
            other.get("/school-plan?q=가람")
            r = other.post("/school-plan", {**FORM, "i": "0"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(PlanRequest.objects.filter(user=second).count(), 1)

    def test_bad_school_index_is_a_form_error_not_500(self):
        self.client.get("/school-plan?q=새솔")
        for i in ("5", "-1", "x", "", "٣", "9" * 5000):
            with self.subTest(i=i[:5]):
                r = Client(raise_request_exception=False)
                r.force_login(self.user)
                r.get("/school-plan?q=새솔")
                self.assertEqual(r.post("/school-plan", {**FORM, "i": i}).status_code, 400)
        self.assertFalse(PlanRequest.objects.exists())

    def test_quote_escapes_values(self):
        r = self.apply(by_name="<script>x</script>", position='"><img src=y>')
        body = self.client.get(r["Location"]).content.decode()
        self.assertNotIn("<script>x", body)
        self.assertNotIn("<img src=y", body)

    def test_quote_not_found_for_guest_or_anon(self):
        r = self.apply()
        anon = Client().get(r["Location"])
        self.assertEqual(anon.status_code, 302)
        self.assertNotIn(PlanRequest.objects.get().key, anon["Location"].split("?")[0])

    def test_renewal_full_circle(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="가람초", plan="school",
                                  paid_until=timezone.localdate() + timedelta(days=20))
        hub = Space.objects.create(school=s, kind="school", grade=0, name="가람초")
        Member.objects.create(space=hub, user=self.user, name="이수빈", cls="교무실", group="office", is_manager=True)
        self.assertContains(self.client.get(f"/s/{hub.token}/school"), "학교 플랜 20일 남음")
        self.assertEqual(self.client.post("/school-plan", FORM).status_code, 303)
        call_command("school_plan", "1", *ON, stdout=StringIO())
        s.refresh_from_db()
        self.assertEqual((s.paid_until, s.plan_requests.get().status), (UNTIL, "done"))
        self.assertEqual(self.client.post("/school-plan", FORM).status_code, 409)
        self.assertEqual(Member.objects.in_space(hub).filter(is_manager=True).count(), 1)


class PlanBellEdges(Base):
    def setUp(self):
        super().setUp()
        self.s = School.objects.create(neis_atpt="J10", neis_code="1", name="가람초", plan="school",
                                       paid_until=timezone.localdate() + timedelta(days=30))
        self.hub = Space.objects.create(school=self.s, kind="school", grade=0, name="가람초")
        self.boss = Member.objects.create(space=self.hub, user=self.user, name="이수빈", cls="교무실", group="office", is_manager=True)
        self.boss2 = Member.objects.create(space=self.hub, user=google("b2"), name="박둘", cls="교무실", group="office", is_manager=True)
        self.plain = Member.objects.create(space=self.hub, user=google("p"), name="김셋", cls="4-1", group="g4")

    def test_every_manager_once_not_members_read_stays_single(self):
        self.assertEqual(bell.remind(at9()), 2)
        self.assertFalse(Notification.objects.filter(member=self.plain).exists())
        n = Notification.objects.get(member=self.boss)
        self.assertTrue(n.pushed)
        self.client.post(f"/s/{self.hub.token}/bell/{n.key}")
        n.refresh_from_db()
        self.assertTrue(n.read)
        for later in range(0, 23):  # 30일 → 8일 남을 때까지 같은 단계
            self.assertEqual(bell.remind(at9(later)), 0, later)

    def test_bell_list_says_plan_and_click_goes_to_plan_page(self):
        bell.remind(at9())
        n = Notification.objects.get(member=self.boss)
        body = self.client.get(f"/s/{self.hub.token}/bell").content.decode()
        self.assertIn(">플랜<", body)
        self.assertNotIn(">기기<", body)
        self.assertEqual(self.client.post(f"/s/{self.hub.token}/bell/{n.key}")["Location"], "/school-plan")

    def test_turned_off_school_quiet(self):
        call_command("school_plan", "1", "--off", stdout=StringIO())
        self.assertEqual(bell.remind(at9()), 0)

    def test_promoted_manager_gets_current_stage(self):
        bell.remind(at9())
        Member.objects.filter(pk=self.plain.pk).update(is_manager=True)
        self.assertEqual(bell.remind(at9()), 1)
        self.assertEqual(Notification.objects.get(member=self.plain).text, Notification.objects.get(member=self.boss).text)

    def test_group_row_membership_changes(self):
        c = Collection.objects.create(space=self.hub, created_by=self.boss, title="전 학년 취합", source=Collection.Source.SHEET,
                                      unit_head=["학년"], due=timezone.now() + timedelta(hours=30))
        c.fields.create(pos=0, name="값", kind="text")
        row = Row.objects.create(collection=c, pos=0, unit="4학년", who="", group="g4")
        empty = Row.objects.create(collection=c, pos=1, unit="6학년", who="", group="g6")
        now = at9()
        Collection.objects.filter(pk=c.pk).update(due=now + timedelta(days=1))
        Notification.objects.filter(kind="plan").delete()
        School.objects.filter(pk=self.s.pk).update(paid_until=UNTIL)
        self.assertEqual(bell.remind(now), 1)  # g4는 김셋 한 명, g6은 아무도 없다
        self.assertFalse(Notification.objects.filter(row=empty).exists())
        newcomer = Member.objects.create(space=self.hub, user=google("n"), name="새사람", cls="4-2", group="g4")
        self.assertEqual(bell.remind(now), 1)  # 새로 온 사람만, 있던 사람은 다시 안 간다
        self.assertEqual(Notification.objects.filter(row=row, member=newcomer).count(), 1)
        Member.objects.filter(pk=self.plain.pk).update(group="g6")
        self.assertEqual(bell.remind(now), 1)  # 옮긴 그룹 줄로 한 번
        self.assertEqual(Notification.objects.filter(row=empty, member=self.plain).count(), 1)
        Member.objects.filter(pk=self.plain.pk).update(group="g4")
        self.assertEqual(bell.remind(now), 0)  # 돌아와도 같은 줄·같은 날은 그대로
        newcomer.delete()
        self.assertFalse(Notification.objects.filter(row=row, member_id=newcomer.pk).exists())


class NoticeRetentionEdges(Base):
    def notice(self, school, grade=0):
        hub = Space.objects.create(school=school, kind="school", grade=grade, name=school.name)
        return Notice.objects.create(space=hub, title="공지", by_name="이수빈")

    def test_clock_starts_at_plan_end_each_school_alone(self):
        today = timezone.localdate()
        ended = School.objects.create(neis_atpt="J10", neis_code="1", name="끝난초", plan="school", paid_until=today - timedelta(days=366))
        edge = School.objects.create(neis_atpt="J10", neis_code="2", name="경계초", plan="school", paid_until=today - timedelta(days=365))
        renewed = School.objects.create(neis_atpt="J10", neis_code="3", name="이은초", plan="school", paid_until=today - timedelta(days=500))
        gone, kept, back = self.notice(ended), self.notice(edge), self.notice(renewed)
        School.objects.filter(pk=renewed.pk).update(paid_until=UNTIL)
        n = retention.purge(timezone.now(), dry_run=True)
        self.assertEqual(n["notices"], 1)
        self.assertTrue(Notice.objects.filter(pk=gone.pk).exists())  # dry-run은 지우지 않는다
        retention.purge(timezone.now())
        self.assertEqual(set(Notice.objects.values_list("pk", flat=True)), {kept.pk, back.pk})

    def test_off_does_not_push_past_end_forward_or_reset(self):
        past = timezone.localdate() - timedelta(days=100)
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="끝난초", plan="school", paid_until=past)
        call_command("school_plan", "1", "--off", stdout=StringIO())
        call_command("school_plan", "1", "--off", stdout=StringIO())
        s.refresh_from_db()
        self.assertEqual((s.plan, s.paid_until), ("free", past))


class QaFixes(Base):
    """O1 QA 수정분: 아주 긴 숫자 500, HEAD, 🔔 표시, 저장 한도."""

    LONG = "9" * 5000

    def test_long_index_is_form_error_not_500(self):
        self.client.get("/school-plan?q=새솔")
        for bad in (self.LONG, "٣" * 5000, "²"):
            r = self.client.post("/school-plan", {**FORM, "i": bad})
            self.assertContains(r, "학교를 찾아 골라 주세요", status_code=400)
        self.assertFalse(PlanRequest.objects.exists())

    def test_long_counts_are_form_errors(self):
        self.client.get("/school-plan?q=새솔")
        self.assertEqual(self.client.post("/school-plan", {**FORM, "i": "0", "students": self.LONG}).status_code, 400)

    def test_same_pattern_elsewhere(self):
        from decimal import Decimal

        from collect.hwpform import ITEM_KEY, ROW_KEY, _roster_class
        from collect.recipients import _class_no
        from collect.views import _fmt
        from hwpfiles.merge import head_value
        from spaces.school import _order

        self.assertIsNone(ITEM_KEY.match(f"item_{self.LONG}_role"))
        self.assertIsNone(ROW_KEY.match(f"row_{self.LONG}"))
        self.assertEqual(_fmt(Decimal("1E+5000")).count(","), 1666)
        self.assertEqual([_fmt(Decimal(x)) for x in ("1000", "1234.50", "0")], ["1,000", "1,234.5", "0"])
        self.assertIsNone(_class_no("4-²"))
        self.assertIsNone(_roster_class({"member": None, "unit": "4-²"}, 4))
        self.assertIsNone(_roster_class({"member": None, "unit": "²-1"}, 0))
        self.assertEqual(head_value("3", 0, {}), "4")
        for weird in (self.LONG, "²"):
            with mock.patch("hwpfiles.merge.text", return_value="받는 곳"):
                self.assertEqual(head_value(weird, 0, {}), "받는 곳")
        seat = Member(cls="4-²", position="", group="g4")
        self.assertEqual(_order(seat), (1, 0))

    def test_bell_labels(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=s, kind="school", grade=0, name="새솔")
        me = Member.objects.create(space=hub, user=self.user, name="이수빈", cls="교무실", is_manager=True)
        for kind in ("vendor", "meal"):
            Notification.objects.create(space=hub, member=me, kind=kind, day=timezone.localdate(), text=kind, link="/", pushed=True)
        html = self.client.get(f"/s/{hub.token}/bell").content.decode()
        self.assertIn(">업체<", html)
        self.assertIn(">급식<", html)
        self.assertNotIn(">기기<", html)

    def test_every_stored_file_kind_counts_toward_quota(self):
        from hwpfiles.models import SourceFile
        from meals.models import MealPhoto
        from notices.models import Notice, NoticeFile
        from repairs.models import Repair

        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔")
        hub = Space.objects.create(school=s, kind="school", grade=0, name="새솔")
        NoticeFile.objects.create(notice=Notice.objects.create(space=hub, title="공지", by_name="가"), name="a", kind="pdf", stored=1, file="notice/a")
        Repair.objects.create(space=hub, cat="info", title="요청", by_name="가", stored=10, photo="repair/a")
        MealPhoto.objects.create(space=hub, date=timezone.localdate(), stored=100, photo="meal/a")
        self.assertEqual(SourceFile.used_bytes(hub), 111)
        self.assertEqual(SourceFile.used_bytes(), 111)


class HeadTests(Base):
    """curl -I·링크 미리보기: 공개 화면은 HEAD에 200. 토큰 주소는 HEAD로 아무것도 쓰지 않는다."""

    def writes(self, ctx):
        return [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]

    def test_public_pages_answer_head(self):
        for url in ("/", "/start", "/forms", "/partners", "/school-plan", "/sitemap.xml", "/privacy", "/terms"):
            with CaptureQueriesContext(connection) as ctx:
                r = Client().head(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(self.writes(ctx), [], url)

    def test_logged_in_home_head_redirects_as_get(self):
        self.assertEqual(self.client.head("/").status_code, self.client.get("/").status_code)

    def test_token_links_write_nothing_on_head(self):
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=s, kind="school", grade=0, name="새솔")
        me = Member.objects.create(space=hub, user=self.user, name="이수빈", cls="교무실", is_manager=True)
        c = Collection.objects.create(space=hub, created_by=me, title="걷기", source="sheet", unit_head=["이름"])
        row = Row.objects.create(collection=c, pos=0, unit="가", who="가", member=me)
        tv = MealSettings.objects.create(space=hub)
        account, vendor_token = VendorAccount.issue(hub, "업체", "담당", UNTIL, me)
        _, phone_token = PhotoLink.issue(hub, timezone.localdate(), me)
        urls = (f"/m/{phone_token}", f"/c/{c.link_token}", f"/c/{c.link_token}/u/{row.key}", f"/tv/{tv.tv_token}", f"/tv/{tv.tv_token}/data",
                f"/v/{vendor_token}", f"/j/{hub.invite_token}", f"/n/{c.key}")
        for url in urls:
            anon = Client()
            with CaptureQueriesContext(connection) as ctx:
                r = anon.head(url)
            self.assertLess(r.status_code, 500, url)
            self.assertEqual(self.writes(ctx), [], url)
        self.assertFalse(VendorAccess.objects.filter(account=account).exists())
        self.assertFalse(Cell.objects.filter(row=row).exists())
        self.assertEqual(Client().head(f"/tv/{tv.tv_token}").status_code, 405)  # 그대로 GET만
        self.assertEqual(Client().head(f"/v/{vendor_token}").status_code, 405)
