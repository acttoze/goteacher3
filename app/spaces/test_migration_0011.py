"""spaces 0011(Attendance.key): 널 허용으로 더하고 → 행마다 고유 토큰 → unique·not null. 기존 행이 있는 DB와 새 DB 모두."""
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE, AFTER = [("spaces", "0010_plan_request")], [("spaces", "0011_attendance_key")]


class AttendanceKeyMigrationTests(TransactionTestCase):
    def migrate(self, target):
        ex = MigrationExecutor(connection)
        ex.loader.build_graph()
        ex.migrate(target)
        return ex.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_existing_rows_get_unique_keys(self):
        old = self.migrate(BEFORE)
        School, Space, Attendance = (old.get_model("spaces", n) for n in ("School", "Space", "Attendance"))
        s = School.objects.create(neis_atpt="J10", neis_code="1", name="옛초")
        sp = Space.objects.create(school=s, kind="school", grade=0, name="옛초", token="t-mig", invite_token="i-mig")
        for i in range(5):
            Attendance.objects.create(space=sp, date=date(2026, 11, 6), name=f"이름{i}", kind="출장")
        new = self.migrate(AFTER)
        keys = list(new.get_model("spaces", "Attendance").objects.values_list("key", flat=True))
        self.assertEqual(len(keys), 5)
        self.assertEqual(len(set(keys)), 5)
        self.assertTrue(all(k and len(k) >= 32 for k in keys))

    def test_fresh_db_field_is_unique_not_null_with_default(self):
        self.migrate(BEFORE)
        apps = self.migrate(AFTER)
        f = apps.get_model("spaces", "Attendance")._meta.get_field("key")
        self.assertFalse(f.null)
        self.assertTrue(f.unique)
        self.assertTrue(callable(f.default))
