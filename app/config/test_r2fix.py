"""R2 통합 검증 마지막 수정(F1·F2·F3, 나간 사람 이름, 플랜 재개와 업체). 화면 동작은 브라우저로 따로 본다."""
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from collect.models import Cell, Collection, Field, Row
from repairs.models import Repair, VendorAccount
from spaces.merge import open_school
from spaces.models import Member, School, Space

U = get_user_model()


def google(sub):
    return U.objects.create(username=f"google:{sub}", email=f"{sub}@x.kr", first_name=sub)


def school(code="1"):
    return School.objects.create(neis_atpt="J10", neis_code=code, name=f"학교{code}")


class UploadFormMarkupTests(SimpleTestCase):
    root = Path(settings.BASE_DIR)
    js = root.parent / "web/static/js"

    def test_forms_use_common_handler(self):
        for path in ("notices/templates/notices/form.html", "repairs/templates/repairs/form.html", "collect/templates/collect/new.html"):
            self.assertRegex((self.root / path).read_text(), r"<form[^>]*\bdata-upload-form\b", path)
        gt = (self.js / "gt.js").read_text()
        self.assertIn("[data-upload-form]", gt)
        self.assertIn("r.redirected", gt)
        phone = (self.js / "meal_phone.js").read_text()
        self.assertIn("fetch(", phone)
        self.assertIn("다시 올리기", phone)

    def test_collect_measures_body_before_sending(self):
        self.assertIn("URLSearchParams", (self.js / "collect_new.js").read_text())
        self.assertIn("data-max-body", (self.root / "collect/templates/collect/new.html").read_text())

    def test_django_limit_equals_body_limit(self):
        self.assertEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, settings.MAX_REQUEST_BYTES)
        self.assertEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 3 * 1024 * 1024)

    def test_no_small_type_in_hwp_screens(self):
        css = (self.root.parent / "web/static/hwp/preview.css").read_text()
        for selector in (".filebar .ext", ".pv-page .pv-no", ".cand .tag "):
            line = next(x for x in css.splitlines() if x.startswith(selector))
            self.assertIn("15px", line, selector)
        detail = (self.root / "collect/templates/collect/detail.html").read_text()
        self.assertNotRegex(detail, r"\.fl \.ext \{[^}]*font-size: 13px")


class GoneNameOnRowsTests(TestCase):
    def setUp(self):
        self.g = Space.objects.create(school=school(), grade=4, name="4")
        self.gone = Member.objects.create(space=self.g, user=google("gone"), name="김나감", cls="4-1")
        self.stay = Member.objects.create(space=self.g, user=google("stay"), name="박남음", cls="4-2")
        self.c = Collection.objects.create(space=self.g, created_by=self.stay, title="모으기", source="sheet")
        self.f = Field.objects.create(collection=self.c, pos=0, name="값", kind="text")

    def row(self, pos, **kw):
        return Row.objects.create(collection=self.c, pos=pos, **{"unit": "4-1", "who": "김나감", **kw})

    def run_cmd(self):
        out = StringIO()
        call_command("delete_account", "gone", stdout=out)
        return out.getvalue()

    def test_unsent_row_loses_name_screen_and_csv(self):
        r = self.row(0, member=self.gone)
        self.run_cmd()
        r.refresh_from_db()
        self.assertEqual(r.who, "")
        self.client.force_login(self.stay.user)
        base = f"/s/{self.g.token}/c/{self.c.key}"
        for url in (f"{base}/", f"{base}/live", f"{base}/export.csv"):
            resp = self.client.get(url)
            body = b"".join(resp.streaming_content) if resp.streaming else resp.content
            self.assertNotIn("김나감", body.decode("utf-8", "replace"), url)

    def test_pasted_row_with_same_name_too(self):
        r = self.row(0)
        self.run_cmd()
        r.refresh_from_db()
        self.assertEqual(r.who, "")

    def test_name_as_unit_cleared(self):
        r = self.row(0, unit="김나감", member=self.gone)
        self.run_cmd()
        r.refresh_from_db()
        self.assertNotIn("김나감", str(r))

    def test_sent_row_keeps_name(self):
        r = self.row(0, member=self.gone)
        Cell.objects.create(row=r, field=self.f, value="값", by="김나감")
        self.run_cmd()
        r.refresh_from_db()
        self.assertEqual(r.who, "김나감")
        self.assertEqual(Cell.objects.get(row=r).by, "김나감")

    def test_namesake_pasted_row_kept(self):
        Member.objects.create(space=self.g, user=google("same"), name="김나감", cls="4-3")
        pasted = self.row(0, unit="4-3")
        mine = self.row(1, member=self.gone)
        self.run_cmd()
        pasted.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((pasted.who, mine.who), ("김나감", ""))

    def test_other_space_untouched(self):
        other = Space.objects.create(school=school("2"), grade=4, name="4")
        c = Collection.objects.create(space=other, title="t", source="sheet")
        r = Row.objects.create(collection=c, pos=0, unit="4-1", who="김나감")
        self.run_cmd()
        r.refresh_from_db()
        self.assertEqual(r.who, "김나감")

    def test_empty_extra_line_of_multi_goes_sent_stays_together(self):
        Collection.objects.filter(pk=self.c.pk).update(multi=True)
        sent = self.row(0, member=self.gone)
        Cell.objects.create(row=sent, field=self.f, value="값", by="김나감")
        empty = self.row(1, member=self.gone)
        self.run_cmd()
        self.assertFalse(Row.objects.filter(pk=empty.pk).exists())
        sent.refresh_from_db()
        self.assertEqual(sent.who, "김나감")

    def test_output_warns_about_names_typed_in_repairs(self):
        hubs = school("3")
        hub = Space.objects.create(school=hubs, kind="school", grade=0, name="학교")
        School.objects.filter(pk=hubs.pk).update(plan="school", paid_until=timezone.localdate() + timedelta(days=9))
        Member.objects.create(space=hub, user=google("boss"), name="관리", cls="교무실", group="office", is_manager=True)
        t = Member.objects.create(space=hub, user=google("typed"), name="최글자", cls="4-1", group="g4")
        Repair.objects.create(space=hub, cat="facility", title="최글자 교실 전등", by=t, by_name="최글자")
        Repair.objects.create(space=hub, cat="facility", title="복도", note="최글자 선생님 확인", by=t, by_name="최글자")
        out = StringIO()
        call_command("delete_account", "typed", stdout=out)
        self.assertIn("제목·메모에 이름이 든 수리 요청 2건", out.getvalue())
        self.assertNotIn("최글자", out.getvalue())

    def test_documents_match(self):
        from accounts.management.commands.delete_account import Command
        self.assertIn("안 낸", Command.help)
        from django.test import Client
        self.assertIn("아직 내지 않은 모으기 줄의 이름은 지웁니다", Client().get("/privacy").content.decode())


class VendorAfterResumeTests(TestCase):
    def setUp(self):
        self.s = school()
        g = Space.objects.create(school=self.s, grade=4, name="4")
        self.m = Member.objects.create(space=g, user=google("m"), name="관리", cls="4-1")
        self.hub = open_school(self.s, timezone.localdate() + timedelta(days=30), manager=self.m)
        self.m.refresh_from_db()
        self.client.force_login(self.m.user)
        self.from_ = timezone.now() - timedelta(days=20)
        VendorAccount.issue(self.hub, "업체", "담당", self.m, visible_from=self.from_)

    def issue(self):
        r = self.client.post(f"/s/{self.hub.token}/repairs/vendor", {
            "name": "새 업체", "contact_name": "새 담당"})
        self.assertEqual(r.status_code, 200)
        return VendorAccount.current(self.hub)

    def resume(self):
        call_command("school_plan", "1", "--off", stdout=StringIO())
        call_command("school_plan", "1", "--on", "--until", f"{timezone.localdate() + timedelta(days=60):%Y-%m-%d}", stdout=StringIO())

    def test_issue_after_resume_keeps_visible_from(self):
        self.resume()
        self.assertEqual(self.issue().visible_from, self.from_)

    def test_request_in_progress_stays_visible(self):
        r = Repair.objects.create(space=self.hub, cat="info", title="처리 중", by=self.m, by_name="관리")
        Repair.objects.filter(pk=r.pk).update(created_at=timezone.now() - timedelta(days=5))
        self.resume()
        self.assertIn(r, self.issue().repairs())

    def test_manual_revoke_starts_fresh(self):
        self.client.post(f"/s/{self.hub.token}/repairs/vendor/revoke")
        before = timezone.now()
        self.assertGreaterEqual(self.issue().visible_from, before)

    def test_manual_revoke_after_resume_starts_fresh(self):
        self.resume()
        self.issue()
        self.client.post(f"/s/{self.hub.token}/repairs/vendor/revoke")
        before = timezone.now()
        self.assertGreaterEqual(self.issue().visible_from, before)

    def test_other_space_cut_does_not_leak(self):
        s2 = school("2")
        g2 = Space.objects.create(school=s2, grade=4, name="4")
        m2 = Member.objects.create(space=g2, user=google("m2"), name="둘", cls="4-1")
        hub2 = open_school(s2, timezone.localdate() + timedelta(days=30), manager=m2)
        self.resume()
        self.assertIsNone(VendorAccount.after_plan_cut(hub2))
