"""플랜이 끝난 채 재개하지 않은 학교의 미해제 업체 계정: 플랜 종료 30일 뒤 행을 지운다."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from hwpfiles import retention
from spaces.models import School, Space

from .models import VendorAccount


class PlanEndTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="가람초")
        self.space = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.account = VendorAccount.objects.create(space=self.space, name="업체", contact_name="담당자", token_hash="t", cookie_hash="c")
        self.today = timezone.localdate()

    def _paid(self, days):
        School.objects.filter(pk=self.school.pk).update(paid_until=self.today + timedelta(days=days))

    def test_paid_or_no_plan_stays(self):
        now = timezone.now()
        self.assertEqual(retention.purge(now)["vendor_accounts"], 0)  # paid_until 없음
        self._paid(10)
        self.assertEqual(retention.purge(now)["vendor_accounts"], 0)

    def test_grace_30_days_then_row_goes(self):
        now = timezone.now()
        self._paid(-30)
        self.assertEqual(retention.purge(now)["vendor_accounts"], 0)
        self._paid(-31)
        self.assertEqual(retention.purge(now, dry_run=True)["vendor_accounts"], 1)
        self.assertTrue(VendorAccount.objects.filter(pk=self.account.pk).exists())
        self.assertEqual(retention.purge(now)["vendor_accounts"], 1)
        self.assertFalse(VendorAccount.objects.filter(pk=self.account.pk).exists())

    def test_renewed_plan_keeps_row(self):
        self._paid(-40)
        self._paid(300)  # 재개하면 기한이 밀린다
        self.assertEqual(retention.purge(timezone.now())["vendor_accounts"], 0)
        self.assertTrue(VendorAccount.objects.filter(pk=self.account.pk).exists())

    def test_privacy_wording(self):
        self.assertContains(self.client.get("/privacy"), "해제하거나 학교 플랜이 끝난 뒤 30일")
