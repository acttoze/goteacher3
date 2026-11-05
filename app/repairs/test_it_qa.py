"""Q5·Q6 QA가 더한 경계: 값이 있는 옛 표의 마이그레이션, 화면 응답에 링크 원문 없음, 복사 단추의 권한·기록, 재발급 권고, 시설 수리 화면."""
from datetime import date, timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase
from django.utils import timezone

from accounts.models import hash_token
from spaces.merge import vacate
from spaces.models import Member, MemberLog, School, Space


from .models import VendorAccount
from .test_vendor import VendorBase


class LinkCopyTests(VendorBase):
    """링크 원문은 복사 단추의 POST 응답에만 있다."""

    def setUp(self):
        super().setUp()
        self.account, self.path = self.issued()
        self.token = self.path.rsplit("/", 1)[1]
        self.url = f"{self.vurl}/link"

    def test_no_page_has_the_link(self):
        r = self.make(cat="info", title="상세 표본")
        for url in (self.it, self.base, f"{self.it}/{r.key}", f"{self.base}/{r.key}", f"{self.vurl}/preview", f"{self.vurl}/log",
                    f"/s/{self.hub.token}/today", f"/s/{self.hub.token}/board", f"/s/{self.hub.token}/school"):
            res = self.manager.get(url)
            self.assertIn(res.status_code, (200, 302), url)
            self.assertNotIn(self.token, res.content.decode(), url)
        self.assertContains(self.manager.get(self.it), "data-link-copy")

    def test_handler_gets_link_and_is_logged(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        res = self.other.post(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.decode().endswith(self.path))
        self.assertEqual(self.client.get(self.path).status_code, 302)
        self.assertIn("no-store", res["Cache-Control"])
        entry = MemberLog.objects.in_space(self.hub).get(after="링크 복사함")
        self.assertEqual((entry.by, entry.name), (self.m5.name, "한빛정보"))
        self.assertEqual(list(self.account.link_seen_by.all()), [self.m5])
        self.assertNotIn(self.token, (entry.by, entry.name, entry.before, entry.after))
        log_page = self.manager.get(f"{self.vurl}/log")
        self.assertContains(log_page, "링크를 복사한 선생님")
        self.assertContains(log_page, self.m5.name)

    def test_ordinary_member_refused_without_trace(self):
        for c in (self.teacher, self.other):
            res = c.post(self.url)
            self.assertEqual(res.status_code, 403)
            self.assertNotIn(self.token, res.content.decode())
        self.assertFalse(MemberLog.objects.in_space(self.hub).filter(after="링크 복사함").exists())
        self.assertFalse(self.account.link_seen_by.exists())

    def test_post_only_and_other_space_404(self):
        self.assertEqual(self.manager.get(self.url).status_code, 405)
        school = School.objects.create(neis_atpt="S10", neis_code="8888888", name="먼학교", plan="school", paid_until=date(2099, 1, 1))
        far = Space.objects.create(school=school, kind="school", grade=0, name="먼학교")
        self.assertEqual(self.manager.post(f"/s/{far.token}/repairs/vendor/link").status_code, 404)

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u4)
        self.assertEqual(c.post(self.url).status_code, 403)


class ReissueHintTests(VendorBase):
    HINT = "링크를 아는 선생님이 담당에서 빠졌어요. 새 링크를 발급하길 권해요."

    def setUp(self):
        super().setUp()
        self.set_handler(self.m4b)
        self.account, _ = self.issued()
        self.assertEqual(self.teacher.post(f"{self.vurl}/link").status_code, 200)  # m4b가 복사
        self.assertNotContains(self.manager.get(self.it), self.HINT)

    def test_copier_loses_role(self):
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=False)
        self.assertContains(self.manager.get(self.it), self.HINT)
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=True)  # 다시 담당이면 권고도 없다
        self.assertNotContains(self.manager.get(self.it), self.HINT)

    def test_copier_leaves_sticks_until_reissue(self):
        vacate(Member.objects.get(pk=self.m4b.pk))
        self.account.refresh_from_db()
        self.assertTrue(self.account.reissue_advised)
        self.assertContains(self.manager.get(self.it), self.HINT)
        self.issue()  # 재발급: 새 행이라 권고가 사라진다
        current = VendorAccount.current(self.hub)
        self.assertNotEqual(current.pk, self.account.pk)
        self.assertFalse(current.reissue_advised)
        self.assertFalse(current.link_seen_by.exists())
        self.assertNotContains(self.manager.get(self.it), self.HINT)

    def test_issuer_loses_role(self):
        self.issue(self.teacher)  # m4b가 발급만 하고 복사는 안 함
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=False)
        self.assertContains(self.manager.get(self.it), self.HINT)

    def test_uninvolved_member_leaving_changes_nothing(self):
        vacate(Member.objects.get(pk=self.m5.pk))
        self.account.refresh_from_db()
        self.assertFalse(self.account.reissue_advised)
        self.assertNotContains(self.manager.get(self.it), self.HINT)


class VendorUntilMigrationTests(TransactionTestCase):
    """until을 지워도 살아 있는 계정·옛 링크는 그대로이고, nonce는 비어 '새 링크 발급' 안내로 간다."""

    OLD = [("repairs", "0003_vendor_plan_cut")]
    NEW = [("repairs", "0005_vendor_reissue")]
    NONCE = [("repairs", "0004_vendor_link_nonce")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _old(self):
        """0003 표로 돌리고 옛 모델과 학교 공간을 만드는 함수를 돌려준다."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.OLD)
        old_model = executor.loader.project_state(self.OLD).apps.get_model("repairs", "VendorAccount")

        def hub(code):
            school = School.objects.create(neis_atpt="S10", neis_code=code, name=f"학교{code}", plan="school", paid_until=date(2099, 1, 1))
            return Space.objects.create(school=school, kind="school", grade=0, name=school.name)
        return old_model, hub

    def _row(self, old_model, space, until, mark):
        return old_model.objects.create(space_id=space.pk, name=mark, contact_name="담당", until=until, token_hash=hash_token(mark),
                                        cookie_hash=hash_token(VendorAccount.cookie_value(mark)))

    def test_expired_accounts_are_revoked_not_revived(self):
        old_model, hub = self._old()
        today = timezone.localdate()
        past = self._row(old_model, hub("7000001"), today - timedelta(days=1), "지난계정")
        last = self._row(old_model, hub("7000002"), today, "오늘까지")
        live = self._row(old_model, hub("7000003"), today + timedelta(days=5), "남은계정")
        MigrationExecutor(connection).migrate(self.NEW)
        self.assertIsNotNone(VendorAccount.objects.get(pk=past.pk).revoked_at)
        self.assertIsNone(VendorAccount.objects.get(pk=last.pk).revoked_at)
        self.assertIsNone(VendorAccount.objects.get(pk=live.pk).revoked_at)
        self.assertEqual(self.client.get("/v/지난계정").status_code, 404)

    def test_reverse_restores_until_with_rows(self):
        old_model, hub = self._old()
        row = self._row(old_model, hub("7000004"), timezone.localdate() + timedelta(days=5), "되돌릴계정")
        MigrationExecutor(connection).migrate(self.NEW)
        MigrationExecutor(connection).migrate(self.OLD)
        back = old_model.objects.get(pk=row.pk)
        self.assertGreater(back.until, timezone.localdate())
        self.assertIsNone(back.revoked_at)
        with connection.cursor() as cur:
            until = next(c for c in connection.introspection.get_table_description(cur, "repairs_vendoraccount") if c.name == "until")
        self.assertFalse(until.null_ok)  # 0003과 같은 표(NOT NULL)

    def test_rows_survive_and_old_link_keeps_working(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.OLD)
        school = School.objects.create(neis_atpt="S10", neis_code="7777777", name="옛학교", plan="school", paid_until=date(2099, 1, 1))
        hub = Space.objects.create(school=school, kind="school", grade=0, name="옛학교")
        old_model = executor.loader.project_state(self.OLD).apps.get_model("repairs", "VendorAccount")
        token = "old-link-token-kept-as-hash-only"
        old_model.objects.create(space_id=hub.pk, name="옛업체", contact_name="옛담당", until=timezone.localdate() + timedelta(days=9),
                                 token_hash=hash_token(token), cookie_hash=hash_token(VendorAccount.cookie_value(token)))
        executor = MigrationExecutor(connection)
        executor.migrate(self.NEW)
        with connection.cursor() as cur:
            columns = [c.name for c in connection.introspection.get_table_description(cur, "repairs_vendoraccount")]
        self.assertNotIn("until", columns)
        account = VendorAccount.objects.get(space=hub)
        self.assertEqual((account.name, account.nonce, account.link_token()), ("옛업체", "", ""))
        self.assertTrue(account.is_open)
        self.assertEqual(self.client.get(f"/v/{token}").status_code, 302)
