"""E3 보관 기간 자동 삭제(purge). 시각은 NOW 로 고정한다.

불변식
- 마감·잠근 때·만든 때가 모두 1년 넘게 지난 모으기는 통째로(칸·줄·제출 값·결과) 지우고, 그 모으기로 만든 올린 파일도.
- 모으기에 안 쓴 올린 파일은 24시간 뒤 지운다. 멈춘 작업(600초)은 실패 + 파일 정리.
- 행이 지워지면 디스크 파일도(커밋 뒤). 되돌린 삭제는 파일을 남긴다. 고아 파일(1시간 넘은 것)은 지운다.
- stored 는 디스크 실제 크기에 맞춘다. 출력은 건수만(제목·이름·값 없음). --dry-run 은 아무것도 지우지 않는다.
"""
import io
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings

from collect.models import Cell, Collection, Field, Row
from library.models import FormDoc
from spaces.models import Member, School, Space

from . import retention
from .models import ResultFile, SourceFile

NOW = datetime(2027, 12, 1, 9, 0, tzinfo=dt_tz.utc)
DAY = timedelta(days=1)
SECRET = "비밀제목김민지"


class PurgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999990", name="파기초")
        cls.sp = Space.objects.create(school=school, grade=3, name="3학년")
        cls.m = Member.objects.create(space=cls.sp, name="김가람", cls="3-1")

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        o = override_settings(MEDIA_ROOT=self.media)
        o.enable()
        self.addCleanup(o.disable)

    # ---- 도우미
    def coll(self, created, due=None, closed=None):
        c = Collection.objects.create(space=self.sp, created_by=self.m, title=SECRET, source="hwp", due=due,
                                      closed_at=closed)
        Collection.objects.filter(pk=c.pk).update(created_at=created)
        f = Field.objects.create(collection=c, pos=0, name="희망")
        r = Row.objects.create(collection=c, pos=0, unit="3-1", who="김가람")
        Cell.objects.create(row=r, field=f, value="학생이름", by="김가람")
        return c

    def source(self, created, collection=None, status=SourceFile.Status.DONE, data=b"PK-hwpx"):
        sf = SourceFile.objects.create(space=self.sp, created_by=self.m, name=f"{SECRET}.hwpx", kind="hwpx",
                                       size=len(data), stored=len(data), status=status, collection=collection)
        sf.original.save("original", ContentFile(data))
        SourceFile.objects.filter(pk=sf.pk).update(created_at=created)
        sf.refresh_from_db()
        return sf

    def result(self, sf, created, status=ResultFile.Status.DONE):
        rf = ResultFile.objects.create(space=self.sp, source=sf, collection=sf.collection, fmt="hwpx",
                                       status=status, stored=5)
        rf.hwpx.save("hwpx", ContentFile(b"PKout"), save=False)
        rf.save()
        ResultFile.objects.filter(pk=rf.pk).update(created_at=created)
        rf.refresh_from_db()
        return rf

    def exists(self, name):
        return default_storage.exists(name)

    def purge(self, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return retention.purge(NOW, **kw)

    # ---- 지난 모으기
    def test_expired_collection_goes_with_values_and_files(self):
        old = self.coll(NOW - 500 * DAY, due=NOW - 366 * DAY)
        sf = self.source(NOW - 500 * DAY, old)
        rf = self.result(sf, NOW - 400 * DAY)
        names = [sf.original.name, rf.hwpx.name]
        n = self.purge()
        self.assertEqual((n["collections"], n["sources"]), (1, 1))
        self.assertFalse(Collection.objects.filter(pk=old.pk).exists())
        self.assertFalse(Cell.objects.exists())
        self.assertFalse(Row.objects.exists())
        self.assertFalse(SourceFile.objects.exists())
        self.assertFalse(ResultFile.objects.exists())
        for name in names:
            self.assertFalse(self.exists(name), name)

    def test_retention_base_is_latest_of_due_closed_created(self):
        keep = [
            self.coll(NOW - 500 * DAY, due=NOW - 364 * DAY),                              # 마감 1년 안
            self.coll(NOW - 500 * DAY, due=NOW - 400 * DAY, closed=NOW - 300 * DAY),     # 잠근 때 1년 안
            self.coll(NOW - 300 * DAY),                                                  # 마감 없음, 만든 지 1년 안
            self.coll(NOW - 400 * DAY, due=NOW + 10 * DAY),                              # 마감이 앞날
        ]
        gone = [self.coll(NOW - 366 * DAY), self.coll(NOW - 800 * DAY, due=NOW - 700 * DAY, closed=NOW - 366 * DAY)]
        self.assertEqual(self.purge()["collections"], 2)
        self.assertEqual(set(Collection.objects.values_list("pk", flat=True)), {c.pk for c in keep})
        self.assertFalse(Collection.objects.filter(pk__in=[c.pk for c in gone]).exists())

    # ---- 안 쓴 올린 파일
    def test_unused_source_after_24h(self):
        live = self.coll(NOW - 10 * DAY)
        old_unused = self.source(NOW - 25 * 3600 * timedelta(seconds=1))
        new_unused = self.source(NOW - 23 * 3600 * timedelta(seconds=1))
        used = self.source(NOW - 10 * DAY, live)
        name = old_unused.original.name
        self.assertEqual(self.purge()["sources"], 1)
        self.assertEqual(set(SourceFile.objects.values_list("pk", flat=True)), {new_unused.pk, used.pk})
        self.assertFalse(self.exists(name))
        self.assertTrue(self.exists(used.original.name))

    # ---- 멈춘 작업
    def test_stuck_jobs_fail_without_opening_screen(self):
        live = self.coll(NOW - DAY)
        S, R = SourceFile.Status, ResultFile.Status
        stuck = self.source(NOW - timedelta(seconds=601), live, status=S.CONVERTING)
        fresh = self.source(NOW - timedelta(seconds=599), status=S.PENDING)
        done = self.source(NOW - DAY, self.coll(NOW - DAY))
        rstuck = self.result(done, NOW - timedelta(seconds=601), status=R.FILLING)
        name, rname = stuck.original.name, rstuck.hwpx.name
        self.assertEqual(self.purge()["stuck"], 2)
        stuck.refresh_from_db()
        rstuck.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual((stuck.status, stuck.error, stuck.stored), (S.FAILED, "Timeout", 0))
        self.assertEqual((rstuck.status, rstuck.error, rstuck.stored), (R.FAILED, "Timeout", 0))
        self.assertEqual(fresh.status, S.PENDING)
        self.assertFalse(self.exists(name))
        self.assertFalse(self.exists(rname))
        self.assertTrue(self.exists(fresh.original.name))

    # ---- 행이 지워지면 파일도
    def test_row_delete_removes_files_after_commit(self):
        sf = self.source(NOW, self.coll(NOW))
        rf = self.result(sf, NOW)
        names = [sf.original.name, rf.hwpx.name]
        with self.captureOnCommitCallbacks(execute=True):
            sf.delete()   # 결과 CASCADE
        for name in names:
            self.assertFalse(self.exists(name), name)

    def test_rolled_back_delete_keeps_file(self):
        sf = self.source(NOW)
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    SourceFile.objects.filter(pk=sf.pk).delete()
                    raise RuntimeError
            except RuntimeError:
                pass
        self.assertTrue(SourceFile.objects.filter(pk=sf.pk).exists())
        self.assertTrue(self.exists(sf.original.name))

    def test_formdoc_delete_removes_file(self):
        d = FormDoc(src="9", slug="x", category="학교", title="t", desc="d", filename="a.hwpx", size=3)
        d.file.save("a.hwpx", ContentFile(b"abc"), save=False)
        d.save()
        name = d.file.name
        with self.captureOnCommitCallbacks(execute=True):
            d.delete()
        self.assertFalse(self.exists(name))

    # ---- 고아 파일·저장 크기
    def test_orphans_and_stored(self):
        sf = self.source(NOW - DAY, self.coll(NOW - DAY), data=b"12345")
        SourceFile.objects.filter(pk=sf.pk).update(stored=999)
        old = default_storage.save("hwp/77/old.out.hwpx", ContentFile(b"x"))
        fresh = default_storage.save("hwp/77/fresh.hwp", ContentFile(b"x"))
        form = default_storage.save("forms/gone.hwp", ContentFile(b"x"))
        os.utime(os.path.join(self.media, fresh), (NOW.timestamp(), NOW.timestamp()))
        n = self.purge()
        self.assertEqual((n["orphans"], n["stored"]), (2, 1))
        self.assertFalse(self.exists(old))
        self.assertFalse(self.exists(form))
        self.assertTrue(self.exists(fresh))        # 1시간 안: 저장 중일 수 있음
        self.assertTrue(self.exists(sf.original.name))
        sf.refresh_from_db()
        self.assertEqual(sf.stored, 5)

    # ---- 명령
    def test_dry_run_changes_nothing_and_prints_counts_only(self):
        old = self.coll(NOW - 400 * DAY)
        sf = self.source(NOW - 400 * DAY, old)
        self.source(NOW - 2 * DAY)
        self.source(NOW - 2 * DAY, status=SourceFile.Status.PENDING)
        out = io.StringIO()
        with mock.patch("django.utils.timezone.now", return_value=NOW), self.captureOnCommitCallbacks(execute=True):
            call_command("purge", "--dry-run", stdout=out)
        text = out.getvalue()
        self.assertIn("(지우지 않음)", text)
        self.assertIn("지난 모으기 1건", text)
        self.assertIn("올린 파일 3건", text)    # 지난 모으기 것 1 + 안 쓴 것 2(멈춘 것 포함)
        self.assertIn("멈춘 작업 1건", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("김가람", text)
        self.assertEqual(SourceFile.objects.count(), 3)
        self.assertTrue(Collection.objects.filter(pk=old.pk).exists())
        self.assertTrue(self.exists(sf.original.name))
        self.assertEqual(SourceFile.objects.filter(status=SourceFile.Status.PENDING).count(), 1)

        out = io.StringIO()
        with mock.patch("django.utils.timezone.now", return_value=NOW), self.captureOnCommitCallbacks(execute=True):
            call_command("purge", stdout=out)
        self.assertNotIn("(지우지 않음)", out.getvalue())
        self.assertFalse(Collection.objects.exists())
        self.assertFalse(SourceFile.objects.exists())
        self.assertFalse(os.listdir(os.path.join(self.media, "hwp", str(self.sp.pk))))


class PurgeLeadTests(PurgeTests):
    """리드 검증(E3) 뒤: 활동 기준·3년 상한·고아 삭제 실패."""

    def test_reopened_without_new_value_is_kept(self):
        """다시 열기(save(update_fields=["closed_at"]))도 마지막 수정으로 센다 — 새 값이 없어도 지우지 않음."""
        c = self.coll(NOW - 500 * DAY, due=NOW - 400 * DAY, closed=NOW - 399 * DAY)
        Collection.objects.filter(pk=c.pk).update(updated_at=NOW - 400 * DAY)
        Cell.objects.filter(row__collection=c).update(at=NOW - 400 * DAY)
        with mock.patch("django.utils.timezone.now", return_value=NOW - DAY):
            c.closed_at = None
            c.save(update_fields=["closed_at"])
        self.assertEqual(self.purge()["collections"], 0)
        Collection.objects.filter(pk=c.pk).update(updated_at=NOW - 366 * DAY)
        self.assertEqual(self.purge()["collections"], 1)

    def test_old_submission_and_edit_still_expire(self):
        c = self.coll(NOW - 500 * DAY, due=NOW - 450 * DAY)
        Collection.objects.filter(pk=c.pk).update(updated_at=NOW - 370 * DAY)
        Cell.objects.filter(row__collection=c).update(at=NOW - 380 * DAY)
        self.assertEqual(self.purge()["collections"], 1)

    def test_far_future_due_capped_at_three_years(self):
        far = datetime(2099, 1, 1, tzinfo=dt_tz.utc)
        gone = self.coll(NOW - 3 * 365 * DAY - DAY, due=far)
        Cell.objects.filter(row__collection=gone).update(at=NOW - DAY)   # 활동이 있어도 상한
        keep = self.coll(NOW - 2 * 365 * DAY, due=far)
        self.purge()
        self.assertFalse(Collection.objects.filter(pk=gone.pk).exists())
        self.assertTrue(Collection.objects.filter(pk=keep.pk).exists())

    def test_orphan_delete_failure_does_not_stop_the_rest(self):
        sf = self.source(NOW - DAY, self.coll(NOW - DAY), data=b"12345")
        SourceFile.objects.filter(pk=sf.pk).update(stored=999)
        a = default_storage.save("hwp/77/a.hwp", ContentFile(b"x"))
        b = default_storage.save("hwp/77/b.hwp", ContentFile(b"x"))
        real = default_storage.delete

        def flaky(name):
            if name == a:
                raise PermissionError(13, "denied")
            return real(name)
        with mock.patch.object(default_storage, "delete", side_effect=flaky):
            n = self.purge()
        self.assertEqual((n["orphans"], n["failed"]), (2, 1))
        self.assertTrue(self.exists(a))
        self.assertFalse(self.exists(b))
        sf.refresh_from_db()
        self.assertEqual(sf.stored, 5)   # 정합까지 이어서 함


class PurgeQaTests(PurgeTests):
    """QA(E3) 경계: 지우면 안 되는 것, 되돌린 purge, dry-run 의 고아·저장 크기, 미디어 밖 파일."""

    def test_reopened_old_collection_with_fresh_submission_is_kept(self):
        """만든 지·마감 1년 넘었어도 어제 다시 열고(closed_at 비움) 새로 낸 값이 있으면 지우지 않는다."""
        c = self.coll(NOW - 500 * DAY, due=NOW - 400 * DAY, closed=NOW - 399 * DAY)
        Collection.objects.filter(pk=c.pk).update(closed_at=None)  # collect.views 다시 열기와 같음
        Cell.objects.filter(row__collection=c).update(at=NOW - DAY)
        self.purge()
        self.assertTrue(Collection.objects.filter(pk=c.pk).exists())

    def test_purge_keeps_members_space_and_other_data(self):
        from collect.models import Notification
        from today.models import Event
        old = self.coll(NOW - 500 * DAY)
        live = self.coll(NOW - 10 * DAY, due=NOW + DAY)
        live_sf = self.source(NOW - 10 * DAY, live)
        live_rf = self.result(live_sf, NOW - 5 * DAY)
        live_rf.file.save("file", ContentFile(b"PKhwp"), save=False)
        live_rf.save()
        n = Notification.objects.create(space=self.sp, member=self.m, row=live.rows.get(), kind="d1",
                                        day=NOW.date(), text="t", link="/x")
        ev = Event.objects.create(space=self.sp, date=NOW.date(), title="행사")
        d = FormDoc(src="91", slug="qa", category="학교", title="t", desc="d", filename="a.hwp", size=3)
        d.file.save("a.hwp", ContentFile(b"abc"), save=False)
        d.save()
        old_t = (NOW - 400 * DAY).timestamp()
        for name in (d.file.name, live_sf.original.name, live_rf.file.name, live_rf.hwpx.name):
            os.utime(os.path.join(self.media, name), (old_t, old_t))
        self.purge()
        self.assertFalse(Collection.objects.filter(pk=old.pk).exists())
        self.assertTrue(Member.objects.filter(pk=self.m.pk).exists())
        self.assertTrue(Space.objects.filter(pk=self.sp.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())
        self.assertTrue(Event.objects.filter(pk=ev.pk).exists())
        self.assertTrue(FormDoc.objects.filter(pk=d.pk).exists())
        for name in (d.file.name, live_sf.original.name, live_rf.file.name, live_rf.hwpx.name):
            self.assertTrue(self.exists(name), name)
        self.assertTrue(Cell.objects.filter(row__collection=live).exists())

    def test_failed_purge_transaction_keeps_rows_and_files(self):
        from django.db.models.signals import pre_delete
        old = self.coll(NOW - 500 * DAY)
        sf = self.source(NOW - 500 * DAY, old)
        rf = self.result(sf, NOW - 400 * DAY)

        def boom(**kw):
            raise RuntimeError("db down")
        pre_delete.connect(boom, sender=Collection, dispatch_uid="qa-boom")
        self.addCleanup(pre_delete.disconnect, sender=Collection, dispatch_uid="qa-boom")
        with self.assertRaises(RuntimeError):
            self.purge()
        self.assertTrue(SourceFile.objects.filter(pk=sf.pk).exists())
        self.assertTrue(self.exists(sf.original.name))
        self.assertTrue(self.exists(rf.hwpx.name))

    def test_dry_run_keeps_orphans_stuck_and_stored(self):
        sf = self.source(NOW - DAY, self.coll(NOW - DAY), data=b"12345")
        SourceFile.objects.filter(pk=sf.pk).update(stored=999)
        stuck = self.source(NOW - DAY, status=SourceFile.Status.CONVERTING)
        orphan = default_storage.save("hwp/77/old.out.hwpx", ContentFile(b"x"))
        old_t = (NOW - DAY).timestamp()
        os.utime(os.path.join(self.media, orphan), (old_t, old_t))
        n = self.purge(dry_run=True)
        self.assertEqual((n["orphans"], n["stored"], n["stuck"]), (1, 1, 1))
        self.assertTrue(self.exists(orphan))
        self.assertEqual(SourceFile.objects.get(pk=sf.pk).stored, 999)
        self.assertEqual(SourceFile.objects.get(pk=stuck.pk).status, SourceFile.Status.CONVERTING)
        self.assertTrue(self.exists(stuck.original.name))

    def test_files_outside_hwp_and_forms_are_untouched(self):
        other = default_storage.save("other/keep.bin", ContentFile(b"x"))
        old_t = (NOW - 400 * DAY).timestamp()
        os.utime(os.path.join(self.media, other), (old_t, old_t))
        self.assertEqual(self.purge()["orphans"], 0)
        self.assertTrue(self.exists(other))

    def test_result_stored_fixed_and_output_has_no_names(self):
        sf = self.source(NOW - DAY, self.coll(NOW - DAY))
        rf = self.result(sf, NOW - DAY)
        ResultFile.objects.filter(pk=rf.pk).update(stored=12345)
        out = io.StringIO()
        with mock.patch("django.utils.timezone.now", return_value=NOW), self.captureOnCommitCallbacks(execute=True), \
                self.assertNoLogs("hwpfiles", level="INFO"):
            call_command("purge", stdout=out)
        rf.refresh_from_db()
        self.assertEqual(rf.stored, len(b"PKout"))
        for bad in (SECRET, "김가람", "학생이름", sf.key, rf.key, "hwp/"):
            self.assertNotIn(bad, out.getvalue())


@override_settings(ROOT_URLCONF="hwpfiles.tests", REDIS_URL="")
class UploadMaliciousQaTests(TestCase):
    """QA(E3): 독립 악성 표본을 업로드 뷰로 올리면 웹(400)이나 worker(실패 + 원본 즉시 삭제)에서 거부된다."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        school = School.objects.create(neis_atpt="S10", neis_code="9999991", name="악성초")
        cls.sp = Space.objects.create(school=school, grade=2, name="2학년")
        cls.u = get_user_model().objects.create(username="google:qa-e3")
        Member.objects.create(space=cls.sp, name="이보안", cls="2-1", user=cls.u)

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        o = override_settings(MEDIA_ROOT=self.media)
        o.enable()
        self.addCleanup(o.disable)
        self.client.force_login(self.u)

    def test_samples_rejected_through_upload_and_worker(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from hwp.tests.test_malicious_qa import SAMPLES
        for name, (make, web) in SAMPLES.items():
            with self.subTest(name):
                SourceFile.objects.all().delete()
                ext = "hwp" if name.startswith("ole") else "hwpx"
                r = self.client.post(f"/s/{self.sp.token}/hwp/upload",
                                     {"file": SimpleUploadedFile(f"x.{ext}", make())})
                if web:
                    self.assertEqual(r.status_code, 400)
                    self.assertFalse(SourceFile.objects.exists())
                else:
                    self.assertEqual(r.status_code, 200)
                    sf = SourceFile.objects.get()
                    self.assertEqual((sf.status, sf.error, sf.stored, sf.original.name, sf.hwpx.name),
                                     (SourceFile.Status.FAILED, "UnsafeFile", 0, "", ""))
                self.assertNotContains(r, "root:", status_code=r.status_code)
                files = [f for _, _, fs in os.walk(self.media) for f in fs]
                self.assertEqual(files, [], "원본이 디스크에 남음")
