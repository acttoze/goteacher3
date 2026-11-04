"""M2 독립 검증: test_tv.py에 없는 경계만. 화면(40px·넘침·끊김·자정)은 브라우저로 따로 봤다."""
import logging
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase

from config.logsafe import SafeErrorFormatter
from spaces.merge import open_school
from spaces.models import Space

from .models import MealPhoto, MealSettings, new_tv_token
from .test_tv import TvBase
from .tests import TUE, jpeg_taken


class SwapTests(TvBase):
    """토큰을 바꿔 끼워 본다."""

    def far_row(self):
        far = open_school(self.far_school, None, self.far_member)
        row, _ = MealSettings.objects.get_or_create(space=far)
        return far, row

    def test_each_token_gets_only_its_own_school_photo(self):
        self.post_photo()
        far, far_row = self.far_row()
        self.assertEqual(self.anon.get(f"/tv/{far_row.tv_token}/photo/20261103").status_code, 404)
        self.assertIsNone(self.anon.get(f"/tv/{far_row.tv_token}/data").json().get("photo"))
        self.assertNotIn(self.school.name, self.anon.get(f"/tv/{far_row.tv_token}/data").content.decode())

    def test_data_of_another_school_never_points_at_this_token(self):
        self.post_photo()
        _, far_row = self.far_row()
        self.assertNotIn(self.row.tv_token, self.anon.get(f"/tv/{far_row.tv_token}/data").content.decode())

    def test_settings_row_of_a_grade_space_is_not_a_tv(self):
        g = Space.objects.create(school=self.school, grade=6, name="6학년")
        row = MealSettings.objects.create(space=g)
        self.assertEqual(self.anon.get(f"/tv/{row.tv_token}/data").status_code, 404)

    def test_space_token_is_not_a_tv_token(self):
        self.assertEqual(self.anon.get(f"/tv/{self.hub.token}/data").status_code, 404)

    def test_past_and_malformed_days_are_404_even_when_a_photo_exists(self):
        self.post_photo(jpeg_taken(TUE - timedelta(days=1)), ymd="20261102")
        self.assertTrue(MealPhoto.objects.in_space(self.hub).filter(date=TUE - timedelta(days=1)).exists())
        for ymd in ("20261102", "2026-11-03", "20261399", "00000000", "20261103x"):
            self.assertEqual(self.anon.get(f"{self.url}/photo/{ymd}").status_code, 404, ymd)


class PhotoEdgeTests(TvBase):
    def test_blank_or_missing_file_is_404_not_500(self):
        p = MealPhoto.objects.create(space=self.hub, date=TUE, photo="", stored=0)
        self.assertEqual(self.anon.get(f"{self.url}/photo/20261103").status_code, 404)
        self.assertIsNone(self.data()["photo"])  # 빈 행은 사진 없음으로
        MealPhoto.objects.filter(pk=p.pk).update(photo="meal/none/gone.jpg")
        self.assertEqual(self.anon.get(f"{self.url}/photo/20261103").status_code, 404)

    def test_photo_headers_say_nothing_about_the_uploader(self):
        words = self.plant()
        r = self.anon.get(f"{self.url}/photo/20261103")
        head = str(dict(r.headers))
        for w in words:
            self.assertNotIn(w, head)
        self.assertNotIn("p.jpg", head)  # 올린 파일 이름
        b"".join(r.streaming_content)

    def test_jpeg_has_no_exif_left(self):
        self.post_photo(jpeg_taken(TUE))
        blob = b"".join(self.anon.get(f"{self.url}/photo/20261103").streaming_content)
        self.assertNotIn(b"Exif", blob[:4096])


class SessionTests(TvBase):
    def test_logged_in_member_gets_the_same_answer_as_a_stranger(self):
        self.plant()
        for path in ("", "/data"):
            a, b = self.anon.get(self.url + path), self.manager.get(self.url + path)
            self.assertEqual(a.content, b.content, path)
            self.assertFalse(b.cookies, path)

    def test_no_by_key_and_referrer_is_kept_in(self):
        self.post_photo()
        self.assertNotIn('"by', self.anon.get(f"{self.url}/data").content.decode())
        self.assertEqual(self.anon.get(self.url)["Referrer-Policy"], "same-origin")

    def test_reissue_needs_csrf(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u5)
        t0 = self.row.tv_token
        self.assertEqual(c.post(f"{self.base}/tv/reissue").status_code, 403)
        self.row.refresh_from_db()
        self.assertEqual(self.row.tv_token, t0)


class LimitAndLogTests(TvBase):
    def test_real_counter_blocks_misses_from_one_address_not_another(self):
        with mock.patch("meals.tv.MISSES_PER_IP", 2):
            codes = [self.anon.get("/tv/x", HTTP_X_FORWARDED_FOR="203.0.113.7").status_code for _ in range(3)]
            self.assertEqual(codes, [404, 404, 429])
            self.assertEqual(self.anon.get("/tv/x", HTTP_X_FORWARDED_FOR="203.0.113.8").status_code, 404)

    def test_error_log_line_masks_the_token(self):
        buf = StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(SafeErrorFormatter())
        lg = logging.getLogger("django.request")
        lg.addHandler(h)
        self.addCleanup(lg.removeHandler, h)
        self.meals.side_effect = RuntimeError(self.row.tv_token)
        r = Client(raise_request_exception=False).get(f"{self.url}/data")
        self.assertEqual(r.status_code, 500)
        self.assertIn("/tv/***/data", buf.getvalue())
        self.assertNotIn(self.row.tv_token, buf.getvalue())


class MigrationTests(TransactionTestCase):
    def test_existing_rows_get_distinct_tokens(self):
        ex = MigrationExecutor(connection)
        ex.migrate([("meals", "0001_initial")])
        apps = ex.loader.project_state([("meals", "0001_initial")]).apps
        School = apps.get_model("spaces", "School")
        Sp = apps.get_model("spaces", "Space")
        Ms = apps.get_model("meals", "MealSettings")
        for i in range(3):
            s = School.objects.create(neis_atpt="Q", neis_code=str(i), name=f"학교{i}")
            Ms.objects.create(space=Sp.objects.create(school=s, kind="school", grade=0, name=f"학교{i}"))
        ex = MigrationExecutor(connection)
        ex.migrate(ex.loader.graph.leaf_nodes("meals"))
        tokens = list(MealSettings.objects.values_list("tv_token", flat=True))
        self.assertEqual(len(set(tokens)), 3)
        self.assertTrue(all(len(t) >= 43 for t in tokens))
        self.assertGreaterEqual(len(new_tv_token()), 43)
