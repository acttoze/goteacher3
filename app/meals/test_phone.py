"""M3 폰 QR로 식판 사진 올리기: 누를 때만 발급·만료·업로드 뒤 닫힘·지우면 끊김·경로 기록·🔔·권한·사진 규칙·PC 반영.
화면(390px·2단계·회전)은 헤드리스 브라우저로 따로 본다."""
import logging
import re
from datetime import datetime, timedelta
from io import BytesIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.files.storage import default_storage
from django.test import Client, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import hash_token
from collect.models import Notification
from config.logsafe import SafeErrorFormatter
from hwpfiles.retention import purge
from repairs.tests import gps_exif, jpeg_bytes, png_header_only, up
from spaces.merge import open_school
from spaces.models import Member

from .models import AFTER_UPLOAD, MAX_LINKS, MAX_UPLOADS, MealPhoto, PhotoLink
from .tests import TUE, Base

KST = ZoneInfo("Asia/Seoul")
SAT = TUE + timedelta(days=4)
TOKEN = re.compile(r'href="[^"]*/m/([\w-]+)"')


def kst(h, m=0, day=3):
    return datetime(2026, 11, day, h, m, tzinfo=KST)


class PhoneBase(Base):
    def setUp(self):
        super().setUp()
        self.anon = Client()

    def token(self, client=None):
        """'폰으로 올리기'를 눌러 QR 주소의 토큰을 얻는다."""
        html = (client or self.writer).post(f"{self.base}/qr", headers={"HX-Request": "true"}).content.decode()
        found = TOKEN.findall(html)
        self.assertEqual(len(found), 1)
        return found[0]

    def send(self, token, data=None, client=None, **fields):
        return (client or self.anon).post(f"/m/{token}", {"photo": up(jpeg_bytes() if data is None else data), **fields})

    def at(self, when):
        p = mock.patch("django.utils.timezone.now", return_value=when)
        p.start()
        self.addCleanup(p.stop)


class IssueTests(PhoneBase):
    def test_opening_the_page_or_polling_makes_no_token(self):
        html = self.writer.get(self.base).content.decode()
        self.assertFalse(PhotoLink.objects.exists())
        self.assertEqual(TOKEN.findall(html), [])
        url = re.search(r'hx-get="([^"]*/meals/live[^"]*)"', html).group(1).replace("&amp;", "&")
        self.writer.get(url, headers={"HX-Request": "true"})
        self.writer.get(f"{self.base}/live", headers={"HX-Request": "true"})  # 다시 그려도
        self.assertFalse(PhotoLink.objects.exists())

    def test_button_only_in_today_cell_for_designated_and_manager(self):
        for c in (self.writer, self.manager):
            html = c.get(self.base).content.decode()
            self.assertEqual(html.count('id="mealQr"'), 1)
            self.assertLess(html.index('aria-label="화 11/3"'), html.index('id="mealQr"'))
            self.assertLess(html.index('id="mealQr"'), html.index('aria-label="수 11/4"'))
            self.assertIn(f'hx-post="{self.base}/qr"', html)

    def test_press_shows_qr(self):
        for c in (self.writer, self.manager):
            html = c.post(f"{self.base}/qr", headers={"HX-Request": "true"}).content.decode()
            self.assertIn("data:image/svg+xml", html)
            self.assertNotIn("<html", html)

    def test_press_without_htmx_draws_the_page_with_qr(self):
        html = self.writer.post(f"{self.base}/qr").content.decode()
        self.assertIn("<html", html)
        self.assertEqual(len(TOKEN.findall(html)), 1)

    def test_get_gives_the_button_back_and_makes_no_token(self):
        html = self.writer.get(f"{self.base}/qr", headers={"HX-Request": "true"}).content.decode()
        self.assertIn(f'hx-post="{self.base}/qr"', html)
        self.assertFalse(PhotoLink.objects.exists())

    def test_qr_takes_itself_down_when_it_ends_without_renewing(self):
        self.at(kst(9))
        html = self.writer.post(f"{self.base}/qr", headers={"HX-Request": "true"}).content.decode()
        self.assertIn('hx-trigger="load delay:7200s"', html)
        self.assertIn(f'hx-get="{self.base}/qr"', html)

    def test_other_teacher_gets_nothing(self):
        self.assertEqual(self.teacher.get(self.base).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/qr").status_code, 403)
        self.assertEqual(self.teacher.get(f"{self.base}/qr").status_code, 403)
        self.assertFalse(PhotoLink.objects.exists())

    def test_other_school_404(self):
        self.assertEqual(self.as_(self.far_user).post(f"{self.base}/qr").status_code, 404)

    def test_no_button_and_no_token_on_weekend_or_when_plan_ended(self):
        self.localdate.return_value = SAT
        self.assertNotIn('id="mealQr"', self.writer.get(self.base).content.decode())
        self.assertEqual(self.writer.post(f"{self.base}/qr").status_code, 400)
        self.localdate.return_value = TUE
        self.expire()
        self.assertNotIn(f'hx-post="{self.base}/qr"', self.writer.get(self.base).content.decode())
        self.assertEqual(self.writer.post(f"{self.base}/qr").status_code, 403)
        self.assertFalse(PhotoLink.objects.exists())

    def test_token_is_long_and_only_its_hash_is_stored(self):
        raw = self.token()
        self.assertGreaterEqual(len(raw), 43)
        row = PhotoLink.objects.values().get()
        self.assertEqual(row["token_hash"], hash_token(raw))
        self.assertNotIn(raw, " ".join(str(v) for v in row.values()))
        self.assertEqual(row["space_id"], self.hub.pk)
        self.assertEqual(row["date"], TUE)
        self.assertEqual(row["by_id"], self.m5.pk)

    def test_expires_after_two_hours_in_the_day(self):
        self.at(kst(9))
        self.token()
        self.assertEqual(PhotoLink.objects.get().expires_at, kst(11))

    def test_expires_at_midnight_when_that_comes_first(self):
        self.at(kst(23, 30))
        self.token()
        self.assertEqual(PhotoLink.objects.get().expires_at, kst(0, 0, day=4))

    def test_old_tokens_per_member_are_limited_and_oldest_dies(self):
        first = self.token()
        for _ in range(MAX_LINKS + 2):
            last = self.token()
        self.assertEqual(PhotoLink.objects.filter(by=self.m5).count(), MAX_LINKS)
        self.assertEqual(self.anon.get(f"/m/{first}").status_code, 404)
        self.assertEqual(self.anon.get(f"/m/{last}").status_code, 200)

    def test_reloading_and_new_tabs_do_not_kill_a_link(self):
        raw = self.token()
        for _ in range(MAX_LINKS + 2):
            self.writer.get(self.base)
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)

    def test_finished_links_do_not_count_against_the_limit(self):
        for _ in range(MAX_LINKS):
            self.token()
        PhotoLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        raw = self.token()
        self.assertEqual(PhotoLink.objects.count(), 1)
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)

    def test_one_teacher_pressing_does_not_kill_anothers_link(self):
        theirs = self.token(self.manager)
        for _ in range(MAX_LINKS + 1):
            self.token()
        self.assertEqual(self.anon.get(f"/m/{theirs}").status_code, 200)

    def test_expired_links_are_purged(self):
        self.token()
        PhotoLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(purge(timezone.now(), dry_run=True)["photo_links"], 1)
        self.assertEqual(PhotoLink.objects.count(), 1)
        purge(timezone.now())
        self.assertFalse(PhotoLink.objects.exists())

    def test_live_link_is_not_purged(self):
        self.token()
        self.assertEqual(purge(timezone.now())["photo_links"], 0)
        self.assertEqual(PhotoLink.objects.count(), 1)


class PhonePageTests(PhoneBase):
    def test_opens_without_login_with_school_and_date_only(self):
        r = self.anon.get(f"/m/{self.token()}")
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.school.name, html)
        self.assertIn("11월 3일 화요일", html)
        self.assertIn('capture="environment"', html)
        self.assertIn("고티처", html)
        for name in (self.m5.name, self.m4.name, self.m4b.name, "쌀밥", "돈육김치찌개", self.hub.token):
            self.assertNotIn(name, html)

    def test_headers(self):
        r = self.anon.get(f"/m/{self.token()}")
        self.assertIn("noindex", r["X-Robots-Tag"])
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(r["Referrer-Policy"], "same-origin")  # no-referrer면 폼의 Origin이 null이 되어 CSRF가 막는다
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])

    def test_no_inline_script_and_no_shell(self):
        html = self.anon.get(f"/m/{self.token()}").content.decode()
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)", html), [])
        self.assertNotRegex(html, r"\son[a-z]+=")
        self.assertNotIn("sidebar", html)
        self.assertNotIn("htmx", html)

    def test_get_changes_nothing(self):
        raw = self.token()
        before = PhotoLink.objects.values().get()
        for _ in range(3):
            self.anon.get(f"/m/{raw}?photo=x&delete=1")
        self.assertEqual(PhotoLink.objects.values().get(), before)
        self.assertFalse(MealPhoto.objects.exists())

    def test_logged_in_viewer_sees_the_same_page(self):
        raw = self.token()
        html = self.writer.get(f"/m/{raw}").content.decode()
        self.assertNotIn(self.m5.name, html)
        self.assertNotIn(self.hub.token, html)

    def test_other_methods_refused(self):
        raw = self.token()
        for call in (self.anon.delete, self.anon.put, self.anon.patch):
            self.assertEqual(call(f"/m/{raw}").status_code, 405)

    def test_token_opens_nothing_else(self):
        raw = self.token()
        self.send(raw)
        for path in (f"/m/{raw}/photo", f"/m/{raw}/delete", f"/m/{raw}/20261103/photo", f"/tv/{raw}", f"/tv/{raw}/data", f"/s/{raw}/meals"):
            self.assertIn(self.anon.get(path).status_code, (302, 404), path)
        self.assertEqual(self.anon.post(f"/m/{raw}/delete").status_code, 404)
        self.assertTrue(MealPhoto.objects.filter(date=TUE).exists())


class ClosedTests(PhoneBase):
    """닫힌 토큰은 어떤 이유든 없는 토큰과 응답이 똑같다."""

    def assert_closed(self, raw):
        unknown = self.anon.get("/m/" + "x" * 43)
        for r in (self.anon.get(f"/m/{raw}"), self.send(raw)):
            self.assertEqual(r.status_code, 404)
            self.assertEqual(r.content, unknown.content)
            self.assertNotIn(self.school.name, r.content.decode())
        self.assertFalse(MealPhoto.objects.exists())

    def test_two_hours_boundary(self):
        self.at(kst(9))
        raw = self.token()
        self.at(kst(10, 59))
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)
        self.at(kst(11))
        self.assert_closed(raw)

    def test_midnight_boundary(self):
        self.at(kst(23, 30))
        raw = self.token()
        self.at(kst(23, 59))
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)
        self.at(kst(0, 0, day=4))
        self.localdate.return_value = TUE + timedelta(days=1)
        self.assert_closed(raw)

    def test_next_day_even_if_expiry_row_says_otherwise(self):
        raw = self.token()
        PhotoLink.objects.update(expires_at=timezone.now() + timedelta(days=3))
        self.localdate.return_value = TUE + timedelta(days=1)
        self.assert_closed(raw)

    def test_maker_loses_the_job(self):
        raw = self.token()
        Member.objects.filter(pk=self.m5.pk).update(can_meal=False)
        self.assert_closed(raw)

    def test_maker_leaves_school(self):
        raw = self.token()
        Member.objects.filter(pk=self.m5.pk).delete()
        self.assert_closed(raw)

    def test_oversized_and_nonsense_tokens_are_404(self):
        for bad in ("x" * 500, "%00", "../../etc"):
            self.assertEqual(self.anon.get(f"/m/{bad}").status_code, 404)

    def test_tv_and_space_tokens_are_not_upload_tokens(self):
        self.assertEqual(self.anon.get(f"/m/{self.hub.token}").status_code, 404)

    def test_hash_itself_does_not_open_it(self):
        raw = self.token()
        self.assertEqual(self.anon.get(f"/m/{hash_token(raw)}").status_code, 404)


class UploadTests(PhoneBase):
    def test_two_steps_saves_photo_for_today_by_the_maker(self):
        r = self.send(self.token())
        self.assertEqual(r.status_code, 200)
        self.assertIn("올렸어요", r.content.decode())
        row = MealPhoto.objects.in_space(self.hub).get()
        self.assertEqual((row.date, row.by), (TUE, self.m5))
        self.assertGreater(row.stored, 0)

    def test_answer_has_no_names_or_photo_url(self):
        html = self.send(self.token()).content.decode()
        for x in (self.m5.name, "/photo", "<img src=\"/s/", self.hub.token, "meal/"):
            self.assertNotIn(x, html)

    def test_replaces_same_day_and_drops_old_file(self):
        raw = self.token()
        self.send(raw)
        old = MealPhoto.objects.get().photo.name
        with self.captureOnCommitCallbacks(execute=True):
            self.send(raw, jpeg_bytes(color=(1, 2, 3)))
        row = MealPhoto.objects.get()
        self.assertNotEqual(row.photo.name, old)
        self.assertFalse(default_storage.exists(old))

    def test_date_fields_in_the_form_are_ignored(self):
        self.send(self.token(), date="2026-11-04", ymd="20261104", space=self.hub.pk + 1, by=self.m4.pk)
        row = MealPhoto.objects.get()
        self.assertEqual((row.date, row.space, row.by), (TUE, self.hub, self.m5))

    def test_csrf_required(self):
        raw = self.token()
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(self.send(raw, client=strict).status_code, 403)
        self.assertFalse(MealPhoto.objects.exists())
        html = strict.get(f"/m/{raw}").content.decode()
        value = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        r = strict.post(f"/m/{raw}", {"photo": up(jpeg_bytes()), "csrfmiddlewaretoken": value})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(MealPhoto.objects.exists())

    def test_upload_limit_then_closed_like_unknown(self):
        raw = self.token()
        for i in range(MAX_UPLOADS):
            r = self.send(raw)
            self.assertEqual(r.status_code, 200, i)
        unknown = self.anon.get("/m/" + "x" * 43)
        for r in (self.send(raw), self.anon.get(f"/m/{raw}")):
            self.assertEqual(r.status_code, 404)
            self.assertEqual(r.content, unknown.content)

    def test_last_upload_offers_no_more(self):
        raw = self.token()
        PhotoLink.objects.update(uses=MAX_UPLOADS - 1)
        html = self.send(raw).content.decode()
        self.assertIn("올렸어요", html)
        self.assertNotIn(f'href="/m/{raw}"', html)

    def test_failed_tries_do_not_use_up_the_token(self):
        raw = self.token()
        for _ in range(MAX_UPLOADS + 1):
            self.assertEqual(self.send(raw, b"not an image").status_code, 400)
        self.assertEqual(PhotoLink.objects.get().uses, 0)
        self.assertEqual(self.send(raw).status_code, 200)

    @mock.patch("meals.phone.TRIES_PER_HOUR", 2)
    def test_failed_tries_per_token_are_limited(self):
        raw = self.token()
        for _ in range(2):
            self.assertEqual(self.send(raw, b"not an image").status_code, 400)
        self.assertEqual(self.send(raw).status_code, 429)
        self.assertFalse(MealPhoto.objects.exists())

    @mock.patch("meals.phone.MISSES_PER_IP", 3)
    def test_unknown_tokens_count_by_ip_and_do_not_block_real_token(self):
        raw = self.token()
        for _ in range(3):
            self.assertEqual(self.anon.get("/m/nope").status_code, 404)
        r = self.anon.get("/m/nope")
        self.assertEqual(r.status_code, 429)
        self.assertIn("noindex", r["X-Robots-Tag"])
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)  # 같은 학교 와이파이의 담당자 폰
        self.assertEqual(self.send(raw).status_code, 200)

    @mock.patch("meals.phone.HITS_PER_MINUTE", 2)
    def test_real_token_counts_per_token(self):
        raw, other = self.token(), self.token()
        for _ in range(2):
            self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)
        r = self.anon.get(f"/m/{raw}")
        self.assertEqual(r.status_code, 429)
        self.assertIn("noindex", r["X-Robots-Tag"])
        self.assertEqual(self.anon.get(f"/m/{other}").status_code, 200)
        self.assertEqual(self.anon.get("/m/nope").status_code, 404)

    @mock.patch("meals.phone.UPLOADS_PER_IP", 1)
    def test_ip_upload_limit(self):
        self.assertEqual(self.send(self.token()).status_code, 200)
        self.assertEqual(self.send(self.token()).status_code, 429)

    def test_no_file(self):
        r = self.anon.post(f"/m/{self.token()}", {})
        self.assertEqual(r.status_code, 400)
        self.assertIn("사진을", r.content.decode())

    def test_plan_ended_shows_notice_and_saves_nothing(self):
        raw = self.token()
        self.expire()
        for r, code in ((self.anon.get(f"/m/{raw}"), 200), (self.send(raw), 403)):
            self.assertEqual(r.status_code, code)
            html = r.content.decode()
            self.assertIn("플랜", html)
            self.assertNotIn('type="file"', html)
        self.assertFalse(MealPhoto.objects.exists())
        self.assertEqual(PhotoLink.objects.get().uses, 0)

    def test_quota_shared_with_other_files(self):
        with override_settings(HWP_TOTAL_QUOTA=1):
            r = self.send(self.token())
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MealPhoto.objects.exists())
        self.assertEqual(PhotoLink.objects.get().uses, 0)


class PhotoRuleTests(PhoneBase):
    def refused(self, data, name="p.jpg"):
        r = self.anon.post(f"/m/{self.token()}", {"photo": up(data, name)})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MealPhoto.objects.exists())
        return r.content.decode()

    def test_gps_removed(self):
        raw = BytesIO()
        Image.new("RGB", (64, 48), (1, 2, 3)).save(raw, "JPEG", exif=gps_exif())
        self.send(self.token(), raw.getvalue())
        data = MealPhoto.objects.get().photo.read()
        self.assertNotIn(b"SecretCamera", data)
        self.assertFalse(Image.open(BytesIO(data)).getexif())

    def test_orientation_applied_once(self):
        raw = BytesIO()
        Image.new("RGB", (64, 48), (1, 2, 3)).save(raw, "JPEG", exif=gps_exif(orientation=6))
        self.send(self.token(), raw.getvalue())
        self.assertEqual(Image.open(MealPhoto.objects.get().photo).size, (48, 64))

    def test_name_says_jpg_but_content_is_not_image(self):
        self.refused(b"<script>alert(1)</script>", "x.jpg")

    def test_broken_jpeg(self):
        self.refused(jpeg_bytes()[:40], "x.jpg")

    def test_pixel_bomb(self):
        self.assertIn("화소", self.refused(png_header_only(30000, 30000), "x.png"))

    def test_scan_bomb(self):
        self.refused(b"\xff\xd8" + b"\xff\xda" * 65)

    def test_over_8mb(self):
        self.assertIn("8MB", self.refused(jpeg_bytes() + b"\0" * (8 * 1024 * 1024)))

    def test_heic_gets_phone_wording(self):
        html = self.refused(b"\0\0\0\x18ftypheic" + b"\0" * 64, "IMG_0001.HEIC")
        self.assertIn("HEIC", html)
        self.assertIn("높은 호환성", html)
        self.assertIn('type="file"', html)  # 다시 찍을 수 있게 폼을 그대로 보여 준다
        self.assertNotIn("IMG_0001", html)


class IsolationTests(PhoneBase):
    def test_token_writes_only_to_its_school(self):
        far = open_school(self.far_school, None, self.far_member)
        self.send(self.token())
        self.assertEqual(MealPhoto.objects.in_space(far).count(), 0)
        self.assertEqual(MealPhoto.objects.in_space(self.hub).count(), 1)

    def test_other_school_member_cannot_poll(self):
        self.assertEqual(self.as_(self.far_user).get(f"{self.base}/live").status_code, 404)

    def test_grade_space_link_is_closed(self):
        from spaces.models import Space
        g = Space.objects.create(school=self.school, grade=6, name="6학년")
        _, raw = PhotoLink.issue(g, TUE, self.m5)
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 404)

    def test_link_from_a_member_of_another_space_is_closed(self):
        far = open_school(self.far_school, None, self.far_member)
        _, raw = PhotoLink.issue(far, TUE, self.m5)
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 404)


class AfterUploadTests(PhoneBase):
    def test_success_shortens_the_link_to_ten_minutes(self):
        self.at(kst(9))
        raw = self.token()
        self.send(raw)
        self.assertEqual(PhotoLink.objects.get().expires_at, kst(9) + AFTER_UPLOAD)
        self.at(kst(9, 9))
        self.assertEqual(self.send(raw, jpeg_bytes(color=(1, 2, 3))).status_code, 200)
        self.assertEqual(PhotoLink.objects.get().expires_at, kst(9) + AFTER_UPLOAD)  # 다시 찍어도 늘어나지 않는다
        self.at(kst(9, 10))
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 404)
        self.assertEqual(self.send(raw).status_code, 404)

    def test_never_lengthens_a_link_about_to_end(self):
        self.at(kst(10, 55))
        raw = self.token()
        end = kst(10, 58)
        PhotoLink.objects.update(expires_at=end)
        self.send(raw)
        self.assertEqual(PhotoLink.objects.get().expires_at, end)

    def test_failed_try_does_not_shorten(self):
        raw = self.token()
        end = PhotoLink.objects.get().expires_at
        self.send(raw, b"nope")
        self.assertEqual(PhotoLink.objects.get().expires_at, end)

    def test_done_page_says_it_closes(self):
        self.assertIn("10분", self.send(self.token()).content.decode())


class DeleteRevokesTests(PhoneBase):
    def test_deleting_the_photo_revokes_every_link_of_that_school_and_day(self):
        mine, theirs = self.token(), self.token(self.manager)
        self.send(mine)
        self.assertEqual(self.writer.post(f"{self.base}/20261103/delete").status_code, 200)
        self.assertFalse(PhotoLink.objects.exists())
        for raw in (mine, theirs):
            self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 404)
            self.assertEqual(self.send(raw).status_code, 404)
        self.assertFalse(MealPhoto.objects.exists())

    def test_deleting_another_day_leaves_todays_link(self):
        self.post_photo(ymd="20261102")
        raw = self.token()
        self.writer.post(f"{self.base}/20261102/delete")
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)

    def test_other_school_link_survives(self):
        far = open_school(self.far_school, None, self.far_member)
        Member.objects.filter(pk=self.far_member.pk).update(is_manager=True)
        self.far_member.refresh_from_db()
        _, raw = PhotoLink.issue(far, TUE, self.far_member)
        self.send(self.token())
        self.writer.post(f"{self.base}/20261103/delete")
        self.assertEqual(PhotoLink.objects.in_space(far).count(), 1)
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 200)


class SourceTests(PhoneBase):
    def test_phone_upload_records_path_link_and_maker(self):
        self.send(self.token())
        link = PhotoLink.objects.get()
        row = MealPhoto.objects.get()
        self.assertEqual((row.source, row.link_ref, row.by_name), (MealPhoto.Source.PHONE, link.pk, self.m5.name))

    def test_pc_upload_records_pc(self):
        self.post_photo()
        row = MealPhoto.objects.get()
        self.assertEqual((row.source, row.link_ref), (MealPhoto.Source.PC, None))

    def test_pc_replace_clears_the_phone_mark(self):
        self.send(self.token())
        self.post_photo()
        row = MealPhoto.objects.get()
        self.assertEqual((row.source, row.link_ref), (MealPhoto.Source.PC, None))

    def test_page_says_how_it_came_and_record_survives_link_and_maker(self):
        self.at(kst(12, 3))
        self.send(self.token())
        want = f"12:03 폰 QR로 올림(QR 만든 사람: {self.m5.name})"
        self.assertIn(want, self.manager.get(self.base).content.decode())
        pk = PhotoLink.objects.get().pk
        purge_at = kst(12, 3) + timedelta(hours=3)
        purge(purge_at)
        self.assertFalse(PhotoLink.objects.exists())
        self.assertEqual(MealPhoto.objects.get().link_ref, pk)
        Member.objects.filter(pk=self.m5.pk).delete()
        self.assertIn(want, self.manager.get(self.base).content.decode())

    def test_pc_upload_shows_plain_line(self):
        self.post_photo()
        self.assertNotIn("폰 QR로", self.writer.get(self.base).content.decode())

    def test_path_is_not_shown_on_phone_or_tv(self):
        html = self.send(self.token()).content.decode()
        self.assertNotIn("만든 사람", html)


class BellTests(PhoneBase):
    def bells(self):
        return Notification.objects.in_space(self.hub).filter(kind=Notification.Kind.MEAL)

    def test_first_upload_rings_nothing(self):
        self.send(self.token())
        self.assertFalse(self.bells().exists())

    def test_replacing_rings_maker_and_managers_without_push(self):
        Member.objects.filter(pk=self.m4b.pk).update(is_manager=False)
        raw = self.token()
        self.post_photo(client=self.manager)
        self.send(raw)
        got = self.bells()
        self.assertEqual({b.member for b in got}, {self.m5, self.m4})
        for b in got:
            self.assertTrue(b.pushed)  # 웹 푸시 묶음에서 뺀다
            self.assertEqual(b.link, f"/s/{self.hub.token}/meals")
            self.assertIn("폰 QR", b.text)
            self.assertIn(self.m5.name, b.text)

    def test_maker_who_is_manager_gets_one(self):
        raw = self.token(self.manager)
        self.post_photo()
        self.send(raw)
        self.assertEqual(self.bells().filter(member=self.m4).count(), 1)

    def test_pc_replace_rings_nothing(self):
        self.post_photo()
        self.post_photo(jpeg_bytes(color=(1, 2, 3)))
        self.assertFalse(self.bells().exists())

    def test_failed_replace_rings_nothing(self):
        self.post_photo()
        self.send(self.token(), b"nope")
        self.assertFalse(self.bells().exists())


class LiveTests(PhoneBase):
    def page_poll(self):
        html = self.writer.get(self.base).content.decode()
        return re.search(r'hx-get="([^"]*/meals/live[^"]*)"', html).group(1).replace("&amp;", "&")

    def test_quiet_when_nothing_changed(self):
        url = self.page_poll()
        self.assertEqual(self.writer.get(url, headers={"HX-Request": "true"}).status_code, 204)

    def test_phone_upload_reaches_open_pc_page_and_takes_qr_down(self):
        url = self.page_poll()
        self.send(self.token())
        r = self.writer.get(url, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('id="mealMain"', html)
        self.assertIn("오늘 사진 올림", html)
        self.assertNotIn("<html", html)
        self.assertEqual(TOKEN.findall(html), [])
        self.assertIn(f'hx-post="{self.base}/qr"', html)

    def test_redraws_after_midnight_without_a_token(self):
        self.at(kst(23, 59))
        url = self.page_poll()
        self.assertEqual(self.writer.get(url, headers={"HX-Request": "true"}).status_code, 204)
        self.at(kst(0, 0, day=4))
        r = self.writer.get(url, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PhotoLink.objects.exists())

    def test_teacher_refused(self):
        self.assertEqual(self.teacher.get(f"{self.base}/live").status_code, 403)

    def test_post_refused(self):
        self.assertEqual(self.writer.post(f"{self.base}/live").status_code, 405)


class M1OrderTests(PhoneBase):
    def test_oversized_file_head_is_not_read(self):
        big = jpeg_bytes() + b"\0" * (8 * 1024 * 1024)
        with mock.patch("meals.views._taken_day") as taken:
            r = self.post_photo(big)
        self.assertEqual(r.status_code, 400)
        taken.assert_not_called()


class BodyLimitTests(PhoneBase):
    def post_declaring(self, path, size):
        return self.anon.post(path, b"x", content_type="application/octet-stream", CONTENT_LENGTH=str(size))

    def test_phone_path_capped_at_9mb(self):
        raw = self.token()
        r = self.post_declaring(f"/m/{raw}", 9 * 1024 * 1024 + 1)
        self.assertEqual(r.status_code, 413)
        self.assertIn("script-src", r["Content-Security-Policy"])

    def test_unknown_token_capped_too(self):
        self.assertEqual(self.post_declaring("/m/nope", 9 * 1024 * 1024 + 1).status_code, 413)

    def test_at_the_limit_passes_the_middleware(self):
        from config.bodylimit import BodyLimitMiddleware
        from django.test import RequestFactory
        rf = RequestFactory()
        mw = BodyLimitMiddleware(lambda r: "passed")
        self.assertEqual(mw(rf.post("/m/x", b"", content_type="text/plain", CONTENT_LENGTH=str(9 * 1024 * 1024))), "passed")
        self.assertEqual(mw(rf.post("/meals/x", b"", content_type="text/plain", CONTENT_LENGTH=str(3 * 1024 * 1024))), "passed")  # 올리는 주소가 아니면 일반 상한


class LogTests(PhoneBase):
    def test_token_masked_in_error_log(self):
        rec = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error: /m/abc-secret", (), None)
        self.assertNotIn("abc-secret", SafeErrorFormatter().format(rec))

    def test_meals_path_still_readable(self):
        rec = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error: /x/meals/live", (), None)
        self.assertIn("/meals/live", SafeErrorFormatter().format(rec))
