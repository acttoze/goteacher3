"""M1 독립 검증(gt-qa): meals/tests.py에 없는 경계만. 사진은 실제 바이트(GPS·XMP·회전·MPO), NEIS는 urlopen 아래에서 끊어 본다."""
import io
import json
import os
import random
import re
import threading
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.cache import cache, caches
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import connection, connections
from django.test import Client, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from PIL import Image

from hwpfiles.models import SourceFile
from hwpfiles.retention import purge
from repairs import photo as repair_photo
from repairs.tests import gps_exif, jpeg_bytes, up
from spaces import neis
from spaces.merge import open_school
from spaces.models import Member, School, Space
from spaces.test_merge import UNTIL, MergeBase

from . import content, views
from .models import MealPhoto, MealSettings
from .tests import MENU, TUE, Base, jpeg_taken

REAL_MEALS = neis.meals
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
R, G, B, Y = (220, 20, 20), (20, 200, 20), (20, 20, 220), (230, 220, 20)


def noise_jpeg(side=1800, quality=95, **save):
    """압축이 잘 안 되는 실제 크기의 JPEG."""
    rnd = random.Random(7)
    im = Image.frombytes("RGB", (side, side), rnd.randbytes(side * side * 3))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality, **save)
    return out.getvalue()


def four_corners(w=64, h=48, fmt="JPEG", **save):
    """네 귀퉁이 색이 다른 사진: 빨 초 / 파 노."""
    im = Image.new("RGB", (w, h))
    for x in range(w):
        for y in range(h):
            im.putpixel((x, y), (R if y < h // 2 else B) if x < w // 2 else (G if y < h // 2 else Y))
    out = io.BytesIO()
    im.save(out, fmt, quality=95, **save)
    return out.getvalue()


def corner_colors(im):
    w, h = im.size
    p = im.convert("RGB")
    return p.getpixel((3, 3)), p.getpixel((w - 4, 3)), p.getpixel((3, h - 4)), p.getpixel((w - 4, h - 4))


def nearest(c):
    return min((R, G, B, Y), key=lambda x: sum(abs(a - b) for a, b in zip(x, c, strict=True)))


def huge_jpeg_header(w, h):
    """SOF 크기만 거대하게 고친 JPEG(픽셀 폭탄)."""
    raw = bytearray(jpeg_bytes())
    i = raw.index(b"\xff\xc0")
    raw[i + 5:i + 9] = h.to_bytes(2, "big") + w.to_bytes(2, "big")
    return bytes(raw)


class QaBase(Base):
    def files(self):
        out = []
        if default_storage.exists("meal"):
            for d in default_storage.listdir("meal")[0]:
                out += [f"meal/{d}/{n}" for n in default_storage.listdir(f"meal/{d}")[1]]
        return out

    def kept(self, ymd=TUE):
        with default_storage.open(self.row(ymd).photo.name) as f:
            return f.read()


class AccessQa(QaBase):
    def test_self_change_to_meal_group_through_real_form_gives_nothing(self):
        r = self.teacher.post(f"/s/{self.hub.token}/school/me", {"group": "meal", "role": "영양교사"})
        self.assertEqual(r.status_code, 302)
        self.m4b.refresh_from_db()
        self.assertEqual(self.m4b.group, Member.Group.MEAL)
        self.assertEqual(self.teacher.get(self.base).status_code, 403)
        self.assertEqual(self.post_photo(client=self.teacher).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/screens", {"up": "cal"}).status_code, 403)
        self.assertNotContains(self.teacher.get(f"/s/{self.hub.token}/today"), f'href="{self.base}"')

    def test_position_text_gives_nothing(self):
        Member.objects.filter(pk=self.m4b.pk).update(position="영양교사", cls="급식소", group=Member.Group.MEAL)
        self.assertEqual(self.teacher.get(self.base).status_code, 403)

    def test_token_swap_between_schools(self):
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        far = self.as_(self.far_user)
        self.post_photo()
        fbase = f"/s/{far_hub.token}/meals"
        for c, url in ((far, self.base), (self.writer, fbase), (self.manager, fbase)):
            self.assertEqual(c.get(url).status_code, 404)
            self.assertEqual(c.get(f"{url}/screens").status_code, 404)
            self.assertEqual(c.get(f"{url}/20261103/photo").status_code, 404)
            self.assertEqual(c.post(f"{url}/20261103/upload", {"photo": up(jpeg_bytes())}).status_code, 404)
            self.assertEqual(c.post(f"{url}/20261103/delete").status_code, 404)
            self.assertEqual(c.post(f"{url}/screens", {"toggle": "menu"}).status_code, 404)
        self.assertEqual(MealPhoto.objects.count(), 1)
        self.assertFalse(MealSettings.objects.exists())
        # 다른 학교 관리자가 우리 회원을 담당자로 지정할 수 없다
        self.assertEqual(far.post(f"/s/{self.hub.token}/school/members/{self.m4b.key}/meal", {"on": "1"}).status_code, 404)
        self.assertEqual(far.post(f"/s/{far_hub.token}/school/members/{self.m4b.key}/meal", {"on": "1"}).status_code, 404)
        self.m4b.refresh_from_db()
        self.assertFalse(self.m4b.can_meal)

    def test_same_school_grade_space_token_goes_nowhere_new(self):
        """합쳐진 옛 동학년 주소로 써도 옛 공간에 쓰이지 않는다."""
        old = f"/s/{self.g4.token}/meals"
        r = self.manager.post(f"{old}/20261103/upload", {"photo": up(jpeg_bytes())})
        self.assertLess(r.status_code, 500)
        self.assertFalse(MealPhoto.objects.filter(space=self.g4).exists())

    def test_unmerged_grade_space_of_paid_school_has_no_meals(self):
        extra = Space.objects.create(school=self.far_school, grade=2, name="먼초 2학년")
        Member.objects.create(space=extra, user=self.far_user, name="다른학교", cls="2-1", can_meal=True, is_manager=True)
        far = self.as_(self.far_user)
        for path in ("", "/screens", "/20261103/photo"):
            self.assertEqual(far.get(f"/s/{extra.token}/meals{path}").status_code, 404)

    def test_expired_plan_free_same_as_past_date(self):
        self.post_photo()
        School.objects.filter(pk=self.school.pk).update(plan="free")
        self.assertEqual(self.manager.get(self.base).status_code, 200)
        self.assertEqual(self.post_photo(ymd="20261104", client=self.manager).status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/screens", {"up": "cal"}).status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/20261103/delete").status_code, 200)
        self.assertFalse(MealPhoto.objects.exists())

    def test_expired_teacher_cannot_delete_and_page_has_delete_only(self):
        self.post_photo()
        self.expire()
        self.assertEqual(self.teacher.post(f"{self.base}/20261103/delete").status_code, 403)
        page = self.writer.get(self.base).content.decode()
        self.assertIn("/delete", page)
        self.assertNotIn('type="file"', page)
        screens = self.writer.get(f"{self.base}/screens").content.decode()
        self.assertNotIn('name="toggle"', screens)
        self.assertNotIn('name="up"', screens)

    def test_removed_writer_loses_access_and_photo_stays_with_no_name(self):
        self.post_photo()
        self.m5.delete()
        self.assertEqual(self.writer.get(self.base).status_code, 404)
        self.assertEqual(self.row().by, None)
        self.assertEqual(self.manager.get(self.base).status_code, 200)

    def test_seat_without_user_refused(self):
        seat = Member.objects.create(space=self.hub, user=None, name="자리만", cls="4-4")
        self.assertEqual(self.manager.post(f"/s/{self.hub.token}/school/members/{seat.key}/meal", {"on": "1"}).status_code, 400)

    def test_designate_button_hidden_for_guest(self):
        from accounts.models import new_guest
        Member.objects.create(space=self.hub, user=new_guest("손님"), name="손님", cls="4-3")
        html = self.manager.get(f"/s/{self.hub.token}/school").content.decode()
        self.assertEqual(html.count("급식 담당자로 지정"), Member.objects.in_space(self.hub).exclude(name="손님").filter(is_manager=False, can_meal=False).count())

    def test_menu_shown_when_expired_for_designated_only(self):
        self.expire()
        link = f'href="{self.base}/screens"'
        today = f"/s/{self.hub.token}/today"
        self.assertContains(self.writer.get(today), link)
        self.assertNotContains(self.teacher.get(today), link)

    def test_get_on_write_routes_405(self):
        for path in ("/20261103/upload", "/20261103/delete"):
            self.assertEqual(self.writer.get(self.base + path).status_code, 405)
        self.assertEqual(self.writer.post(f"{self.base}/20261103/photo").status_code, 405)
        self.assertEqual(self.writer.put(f"{self.base}/screens").status_code, 405)

    def test_csrf_enforced_on_upload_delete_screens_designate(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u4)
        for path in (f"{self.base}/20261103/upload", f"{self.base}/20261103/delete", f"{self.base}/screens", f"/s/{self.hub.token}/school/members/{self.m4b.key}/meal"):
            self.assertEqual(c.post(path).status_code, 403, path)


class PhotoQa(QaBase):
    def test_real_gps_xmp_comment_icc_over_memory_threshold(self):
        """2.5MB를 넘으면 임시 파일로 받는다. GPS·XMP·주석·ICC가 다 빠지고 촬영일도 읽힌다."""
        exif = gps_exif()
        exif.get_ifd(views.EXIF_IFD)[views.DATE_ORIGINAL] = "2026:11:02 09:30:00"
        raw = noise_jpeg(exif=exif, xmp=b"<x:xmpmeta>SecretXmp</x:xmpmeta>", icc_profile=b"SecretICC" * 10, comment=b"SecretComment")
        self.assertGreater(len(raw), 2_621_440)
        self.assertLess(len(raw), repair_photo.MAX_BYTES)
        self.assertIn(b"SecretCamera", raw)
        self.assertIn(b"SecretXmp", raw)
        r = self.post_photo(raw)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "다른 날 찍은 사진 같아요")
        kept = self.kept()
        for word in (b"SecretCamera", b"SecretXmp", b"SecretICC", b"SecretComment", b"Exif\x00", b"xmpmeta", b"ICC_PROFILE"):
            self.assertNotIn(word, kept)
        im = Image.open(io.BytesIO(kept))
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(max(im.size), repair_photo.LONG_SIDE)
        self.assertFalse(im.getexif())
        self.assertNotIn(0x8825, dict(im.getexif()))

    def test_png_text_chunks_removed(self):
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_text("Comment", "SecretPngText")
        out = io.BytesIO()
        Image.new("RGB", (64, 48), R).save(out, "PNG", pnginfo=info)
        self.assertEqual(self.post_photo(out.getvalue(), name="a.png").status_code, 200)
        self.assertNotIn(b"SecretPngText", self.kept())

    def test_orientation_applied_all_rotations(self):
        cases = {3: (Y, B, G, R), 6: (B, R, Y, G), 8: (G, Y, R, B), 1: (R, G, B, Y)}
        for orientation, want in cases.items():
            exif = Image.Exif()
            exif[0x0112] = orientation
            self.post_photo(four_corners(exif=exif))
            im = Image.open(io.BytesIO(self.kept()))
            self.assertEqual(tuple(nearest(c) for c in corner_colors(im)), want, orientation)
            self.assertEqual(im.size, (48, 64) if orientation in (6, 8) else (64, 48), orientation)
            self.assertFalse(im.getexif())

    def test_mpo_phone_photo_accepted(self):
        """갤럭시·아이폰 JPEG는 MPO(사진 두 장)인 경우가 많다."""
        out = io.BytesIO()
        first, second = Image.new("RGB", (64, 48), R), Image.new("RGB", (32, 24), G)
        exif = gps_exif()
        exif.get_ifd(views.EXIF_IFD)[views.DATE_ORIGINAL] = "2026:11:02 09:30:00"
        first.save(out, "MPO", save_all=True, append_images=[second], exif=exif)
        r = self.post_photo(out.getvalue())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "다른 날 찍은 사진 같아요")
        kept = self.kept()
        self.assertNotIn(b"SecretCamera", kept)
        self.assertEqual(Image.open(io.BytesIO(kept)).format, "JPEG")

    def test_other_modes_become_rgb_jpeg(self):
        samples = {}
        for mode, color in (("L", 128), ("CMYK", (0, 0, 0, 0))):
            b = io.BytesIO()
            Image.new(mode, (40, 40), color).save(b, "JPEG")
            samples[f"jpeg-{mode}"] = b.getvalue()
        for mode, color in (("RGBA", (10, 20, 30, 0)), ("P", 3), ("L", 200), ("1", 1), ("I;16", 40000), ("LA", (9, 0))):
            b = io.BytesIO()
            Image.new(mode, (40, 40), color).save(b, "PNG")
            samples[f"png-{mode}"] = b.getvalue()
        for key, data in samples.items():
            r = self.post_photo(data, name="x.png")
            self.assertEqual(r.status_code, 200, key)
            im = Image.open(io.BytesIO(self.kept()))
            self.assertEqual((im.format, im.mode), ("JPEG", "RGB"), key)

    def test_transparent_png_on_white(self):
        b = io.BytesIO()
        Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(b, "PNG")
        self.post_photo(b.getvalue(), name="clip.png")
        self.assertGreater(min(Image.open(io.BytesIO(self.kept())).getpixel((20, 20))), 240)

    def test_name_lies_both_ways(self):
        b = io.BytesIO()
        Image.new("RGB", (64, 48), G).save(b, "PNG")
        self.assertEqual(self.post_photo(b.getvalue(), name="png인데.jpg").status_code, 200)
        self.assertEqual(self.post_photo(jpeg_bytes(), name="jpg인데.png").status_code, 200)
        self.assertEqual(self.post_photo(jpeg_bytes(), name="x.exe").status_code, 200)
        self.assertRegex(self.row().photo.name, r"\.jpg$")

    def test_refused_kinds_leave_nothing_on_disk(self):
        samples = {
            "html.jpg": b"<html><script>alert(1)</script></html>",
            "svg.jpg": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "heic.jpg": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\0" * 64,
            "real.heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\0" * 64,
            "empty.jpg": b"",
            "half.jpg": jpeg_bytes(size=(640, 480))[:300],
            "soi.jpg": b"\xff\xd8\xff\xd9",
        }
        for name, data in samples.items():
            r = self.post_photo(data, name=name)
            self.assertEqual(r.status_code, 400, name)
            self.assertFalse(MealPhoto.objects.exists(), name)
            self.assertEqual(self.files(), [], name)
        r = self.post_photo(samples["real.heic"], name="real.heic")
        self.assertContains(r, "HEIC", status_code=400)

    def test_truncated_mid_scan_refused(self):
        raw = noise_jpeg(side=300)
        r = self.post_photo(raw[: len(raw) // 2])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MealPhoto.objects.exists())

    def test_jpeg_pixel_bomb_refused_without_decoding(self):
        with mock.patch("PIL.ImageFile.ImageFile.load", side_effect=AssertionError("픽셀을 풀면 안 된다")):
            r = self.post_photo(huge_jpeg_header(30000, 30000))
        self.assertContains(r, "화소", status_code=400)
        self.assertFalse(MealPhoto.objects.exists())

    def test_exact_8mb_accepted_and_one_more_refused(self):
        base = jpeg_bytes()
        at = base + b"\0" * (repair_photo.MAX_BYTES - len(base))
        self.assertEqual(len(at), repair_photo.MAX_BYTES)
        self.assertEqual(self.post_photo(at).status_code, 200)
        over = self.post_photo(at + b"\0", ymd="20261104")
        self.assertContains(over, "8MB", status_code=400)
        self.assertEqual(MealPhoto.objects.count(), 1)

    def test_bad_replacement_keeps_old_photo(self):
        self.post_photo()
        before = self.row()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post_photo(b"nope").status_code, 400)
        after = self.row()
        self.assertEqual((after.photo.name, after.stored), (before.photo.name, before.stored))
        self.assertTrue(default_storage.exists(after.photo.name))

    def test_quota_refused_replacement_keeps_old_photo_and_no_new_file(self):
        self.post_photo(jpeg_bytes(size=(64, 48)))
        before = self.row()
        with override_settings(HWP_SCHOOL_QUOTA=SourceFile.used_bytes(self.hub)):
            with self.captureOnCommitCallbacks(execute=True):
                big = noise_jpeg(side=500)
                self.assertEqual(self.post_photo(big).status_code, 400)
        after = self.row()
        self.assertEqual(after.photo.name, before.photo.name)
        self.assertEqual(self.files(), [before.photo.name])

    def test_total_quota_counts_meal_photos(self):
        self.post_photo()
        with override_settings(HWP_TOTAL_QUOTA=SourceFile.used_bytes()):
            self.assertEqual(self.post_photo(ymd="20261104").status_code, 400)
        self.assertEqual(MealPhoto.objects.count(), 1)

    def test_used_bytes_equals_disk_after_many_replaces(self):
        for side in (64, 200, 32, 640):
            with self.captureOnCommitCallbacks(execute=True):
                self.post_photo(noise_jpeg(side=side))
        with self.captureOnCommitCallbacks(execute=True):
            self.post_photo(jpeg_bytes(), ymd="20261105")
        names = self.files()
        self.assertEqual(len(names), 2)
        on_disk = sum(default_storage.size(n) for n in names)
        self.assertEqual(SourceFile.used_bytes(self.hub), self.used0 + on_disk)
        self.assertEqual(SourceFile.used_bytes(), SourceFile.used_bytes(self.hub) + SourceFile.used_bytes(self.far))

    def test_real_rate_limit_31st_refused(self):
        caches["ratelimit"].clear()
        # 사진 처리는 건너뛰지 않되 빠르게
        for i in range(views.UPLOAD_PER_HOUR):
            self.assertEqual(self.post_photo().status_code, 200, i)
        self.assertEqual(self.post_photo().status_code, 429)
        self.assertEqual(self.post_photo(client=self.manager).status_code, 200)  # 사람마다 따로

    def test_photo_file_has_no_exif_and_is_private(self):
        self.post_photo(four_corners(exif=gps_exif()))
        r = self.teacher.get(f"{self.base}/20261103/photo")
        body = b"".join(r.streaming_content)
        self.assertNotIn(b"SecretCamera", body)
        self.assertNotIn("public", r["Cache-Control"])

    def test_replace_in_same_second_changes_photo_url(self):
        """사진은 private, max-age=3600으로 캐시한다. ?v=가 초 단위면 같은 초에 바꾼 새 사진 대신 옛 사진이 한 시간까지 보인다."""
        fixed = timezone.now()
        with mock.patch("django.utils.timezone.now", return_value=fixed):
            first = self.post_photo(jpeg_bytes(color=(1, 2, 3))).content.decode()
            second = self.post_photo(jpeg_bytes(color=(250, 2, 3))).content.decode()
        url = re.compile(r"/meals/20261103/photo\?v=[^\"]+")
        self.assertNotEqual(url.search(first)[0], url.search(second)[0])

    def test_missing_disk_file_is_404_not_500(self):
        self.post_photo()
        default_storage.delete(self.row().photo.name)
        self.assertEqual(self.teacher.get(f"{self.base}/20261103/photo").status_code, 404)
        self.assertEqual(self.writer.get(self.base).status_code, 200)
        self.assertEqual(self.post_photo().status_code, 200)  # 파일이 없어도 교체된다
        self.assertEqual(self.writer.post(f"{self.base}/20261103/delete").status_code, 200)

    def test_delete_nothing_is_404(self):
        self.assertEqual(self.writer.post(f"{self.base}/20261103/delete").status_code, 404)


class WarningQa(QaBase):
    """리드 결정: EXIF 경고는 '그 칸 날짜'와 비교."""

    def test_exif_today_on_tomorrow_cell_warns_other_day(self):
        r = self.post_photo(jpeg_taken(TUE), ymd="20261104")
        self.assertContains(r, "칸이 맞는지")
        self.assertContains(r, "다른 날 찍은 사진 같아요")

    def test_exif_equals_cell_only_cell_warning(self):
        r = self.post_photo(jpeg_taken(date(2026, 11, 5)), ymd="20261105")
        self.assertContains(r, "칸이 맞는지")
        self.assertNotContains(r, "다른 날 찍은")

    def test_warning_is_only_for_that_response(self):
        self.post_photo(jpeg_taken(date(2026, 11, 2)))
        self.assertNotContains(self.writer.get(self.base), "다른 날 찍은")

    def test_png_and_garbage_exif_date_no_warning(self):
        for text in ("        ", "2026:13:99 00:00:00", "abc", ""):
            exif = Image.Exif()
            exif.get_ifd(views.EXIF_IFD)[views.DATE_ORIGINAL] = text
            out = io.BytesIO()
            Image.new("RGB", (32, 32)).save(out, "JPEG", exif=exif)
            r = self.post_photo(out.getvalue())
            self.assertEqual(r.status_code, 200, text)
            self.assertNotContains(r, "다른 날 찍은")


class WeekQa(QaBase):
    def week_of(self, today):
        return views._week(today)

    def test_week_every_weekday_and_year_boundary(self):
        for offset in range(0, 5):
            self.assertEqual(self.week_of(date(2026, 11, 2) + timedelta(days=offset))[0], date(2026, 11, 2))
        self.assertEqual(self.week_of(date(2026, 11, 7))[0], date(2026, 11, 9))
        self.assertEqual(self.week_of(date(2026, 11, 8))[0], date(2026, 11, 9))
        self.assertEqual(self.week_of(date(2026, 12, 31)), [date(2026, 12, 28) + timedelta(days=i) for i in range(5)])
        self.assertEqual(self.week_of(date(2027, 1, 2))[0], date(2027, 1, 4))

    def test_sunday_next_week_upload_and_two_warnings(self):
        self.localdate.return_value = date(2026, 11, 8)
        r = self.post_photo(jpeg_taken(date(2026, 11, 7)), ymd="20261109")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "칸이 맞는지")
        self.assertContains(r, "다른 날 찍은")
        self.assertNotContains(r, "오늘 사진 아직")
        self.assertEqual(self.post_photo(ymd="20261106").status_code, 400)  # 지난주 금요일

    def test_no_meal_day_allows_photo(self):
        """리드 결정: 급식 없는 날도 사진 허용."""
        self.meals.return_value = []
        page = self.writer.get(self.base).content.decode()
        self.assertEqual(page.count('type="file"'), 5)
        self.assertEqual(self.post_photo(ymd="20261106").status_code, 200)

    def test_today_only_one_highlight(self):
        page = self.writer.get(self.base).content.decode()
        self.assertEqual(page.count("mday today"), 1)

    def test_menu_with_xss_is_escaped(self):
        self.meals.return_value = [{**MENU[0], "dishes": [{"name": "<img src=x onerror=alert(1)>", "allergy": [], "self": False}]}]
        self.assertNotContains(self.writer.get(self.base), "<img src=x")


class NeisQa(QaBase):
    """가짜 meals를 걷어내고 urlopen 아래에서 본다."""

    def setUp(self):
        super().setUp()
        p = mock.patch("spaces.neis.meals", REAL_MEALS)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []
        self.answer = lambda url: {"RESULT": {"CODE": "INFO-200"}}
        p = mock.patch("spaces.neis.urlopen", side_effect=self._urlopen)
        p.start()
        self.addCleanup(p.stop)

    def _urlopen(self, url, timeout=None):
        self.calls.append(url)
        got = self.answer(url)
        if isinstance(got, Exception):
            raise got
        return io.BytesIO(got if isinstance(got, bytes) else json.dumps(got).encode())

    @staticmethod
    def row(ymd="20261103", dish="쌀밥"):
        return {"MLSV_YMD": ymd, "DDISH_NM": f"{dish}<br/>김치(9)", "CAL_INFO": "650.1 Kcal", "NTR_INFO": "단백질(g) : 20.5", "ORPLC_INFO": "쌀 : 국내산", "MLSV_FGR": "400"}

    def ok(self, *rows):
        return {"mealServiceDietInfo": [{"head": [{"list_total_count": len(rows)}]}, {"row": list(rows)}]}

    def test_info200_is_no_meal_and_others_are_failure(self):
        r = self.writer.get(self.base)
        self.assertContains(r, "급식 없음")
        self.assertNotContains(r, "불러오지 못했어요")
        for bad in ({"RESULT": {"CODE": "ERROR-500"}}, {"RESULT": {"CODE": "INFO-300"}}, b"<html>not json", {"mealServiceDietInfo": []},
                    URLError("x"), OSError("x"), TimeoutError("x"), HTTPError("u", 503, "x", {}, None)):
            cache.clear()
            self.answer = lambda url, bad=bad: bad
            r = self.writer.get(self.base)
            self.assertEqual(r.status_code, 200, bad)
            self.assertContains(r, "불러오지 못했어요")
            self.assertNotContains(r, "급식 없음")
            self.assertContains(r, "다시 시도")

    def test_retry_recovers_and_without_retry_failure_cached(self):
        self.answer = lambda url: URLError("x")
        self.assertContains(self.writer.get(self.base), "불러오지 못했어요")
        n = len(self.calls)
        self.answer = lambda url: self.ok(self.row())
        self.assertContains(self.writer.get(self.base), "불러오지 못했어요")  # 1분 캐시
        self.assertEqual(len(self.calls), n)
        r = self.writer.get(self.base, {"retry": "1"}, headers={"HX-Request": "true"})
        self.assertContains(r, "쌀밥")
        self.assertNotContains(r, "불러오지 못했어요")
        self.assertNotContains(r, "<aside")

    def test_retry_limit_six_per_minute_is_real(self):
        self.answer = lambda url: URLError("x")
        fixed = 1_800_000_000
        with mock.patch("config.ratelimit.time.time", return_value=fixed):
            self.writer.get(self.base)
            base = len(self.calls)
            for _ in range(views.RETRY_PER_MINUTE):
                cache.delete(neis.FAILS)  # 차단기는 config.test_o2가 본다. 여기서는 빈도 제한만
                self.writer.get(self.base + "?retry=1")
            self.assertEqual(len(self.calls), base + 6)
            for _ in range(5):
                r = self.writer.get(self.base + "?retry=1")
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "불러오지 못했어요")
            self.assertEqual(len(self.calls), base + 6)  # 넘으면 NEIS를 부르지 않는다
        with mock.patch("config.ratelimit.time.time", return_value=fixed + 61):
            self.writer.get(self.base + "?retry=1")
        self.assertEqual(len(self.calls), base + 7)

    def test_retry_does_not_hit_neis_when_cached_ok(self):
        self.answer = lambda url: self.ok(self.row())
        self.writer.get(self.base)
        n = len(self.calls)
        self.writer.get(self.base + "?retry=1")
        self.assertEqual(len(self.calls), n)

    def test_two_schools_do_not_mix(self):
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        far = self.as_(self.far_user)
        self.answer = lambda url: self.ok(self.row(dish="우리밥" if "SD_SCHUL_CODE=7531234" in url else "먼밥"))
        mine, theirs = self.writer.get(self.base), far.get(f"/s/{far_hub.token}/meals")
        self.assertContains(mine, "우리밥")
        self.assertNotContains(mine, "먼밥")
        self.assertContains(theirs, "먼밥")
        self.assertNotContains(theirs, "우리밥")

    def test_failure_of_one_school_does_not_show_at_other_and_retry_only_mine(self):
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        far = self.as_(self.far_user)
        self.answer = lambda url: URLError("x") if "SD_SCHUL_CODE=7531234" in url else self.ok(self.row())
        self.assertContains(self.writer.get(self.base), "불러오지 못했어요")
        self.assertNotContains(far.get(f"/s/{far_hub.token}/meals"), "불러오지 못했어요")

    def test_key_in_url_not_in_page_or_log(self):
        self.answer = lambda url: URLError("x")
        with override_settings(NEIS_KEY="SECRETKEY123"), self.assertLogs("spaces.neis", "WARNING") as logs:
            r = self.writer.get(self.base)
        self.assertNotContains(r, "SECRETKEY123")
        self.assertNotIn("SECRETKEY123", " ".join(logs.output))

    def test_week_uses_next_week_on_weekend_in_request(self):
        self.localdate.return_value = date(2026, 11, 7)
        self.answer = lambda url: self.ok(self.row("20261109"))
        self.assertContains(self.writer.get(self.base), "쌀밥")
        self.assertIn("MLSV_FROM_YMD=20261109", self.calls[-1])
        self.assertIn("MLSV_TO_YMD=20261113", self.calls[-1])

    def test_upload_still_ok_when_neis_down_and_no_meal_in_fail_mode_not_called_none(self):
        self.answer = lambda url: URLError("x")
        r = self.post_photo()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "오늘 사진 올림")

    def test_kcal_missing_row_shows_zero_not_none(self):
        self.answer = lambda url: self.ok({"MLSV_YMD": "20261103", "DDISH_NM": "쌀밥"})
        r = self.writer.get(self.base)
        self.assertContains(r, "0kcal")
        self.assertNotContains(r, "None")


class ScreenQa(QaBase):
    def post(self, *args, **data):
        return self.writer.post(f"{self.base}/screens", data)

    def state(self):
        return {s["key"]: s["on"] for s in MealSettings.screens_of(self.hub)}

    def test_forge_every_way_cannot_turn_photo_off(self):
        url = f"{self.base}/screens"
        self.assertEqual(self.post(toggle="photo").status_code, 400)
        self.assertEqual(self.writer.post(url, {"toggle": ["menu", "photo"]}).status_code, 400)
        self.assertEqual(self.writer.post(url, {"toggle": "photo", "on": "0"}).status_code, 400)
        for extra in ({"up": "photo", "toggle": "photo"}, {"down": "photo", "toggle": "photo"}):
            self.writer.post(url, extra)
        self.writer.post(url, {"toggle": ["photo", "menu"]})
        self.assertTrue(self.state()["photo"])
        self.assertTrue(all(s.get("on", True) or s["key"] != "photo" for s in MealSettings.objects.in_space(self.hub).get().screens))
        # 관리자도 같다
        self.assertEqual(self.manager.post(url, {"toggle": "photo"}).status_code, 400)

    def test_garbage_values_refused_and_nothing_saved(self):
        for bad in ({"toggle": ""}, {"up": "nope"}, {"down": "<script>"}, {"toggle": "PHOTO"}, {"up": ""}, {"sec": "99"}, {}):
            self.assertEqual(self.post(**bad).status_code, 400, bad)
        self.assertFalse(MealSettings.objects.exists())

    def test_turning_everything_off_leaves_photo_and_numbers_right(self):
        for s in content.SCREENS:
            if s["key"] != "photo":
                self.post(toggle=s["key"])
        self.assertEqual([k for k, on in self.state().items() if on], ["photo"])
        r = self.writer.get(f"{self.base}/screens").content.decode()
        self.assertIn('<b class="num">1</b>개', r)
        self.assertIn('<b class="num">7</b>초', r)

    def test_loop_seconds_default_and_after_off(self):
        html = self.writer.get(f"{self.base}/screens").content.decode()
        self.assertIn('<b class="num">8</b>개', html)
        self.assertIn('<b class="num">90</b>초', html)  # 7×6 + 12×2 + 12×2
        html = self.post(toggle="origin").content.decode()
        self.assertIn('<b class="num">7</b>개', html)
        self.assertIn('<b class="num">66</b>초', html)

    def test_order_survives_and_keeps_off_state(self):
        self.post(toggle="week")
        self.post(up="week")
        items = MealSettings.screens_of(self.hub)
        self.assertEqual(items[-2]["key"], "week")
        self.assertFalse(items[-2]["on"])

    def test_new_screen_added_later_comes_on_at_end(self):
        MealSettings.objects.create(space=self.hub, screens=[{"key": "week", "on": False}, {"key": "menu", "on": True}])
        got = MealSettings.screens_of(self.hub)
        self.assertEqual([s["key"] for s in got][:2], ["week", "menu"])
        self.assertEqual(len(got), len(content.SCREENS))

    def test_settings_after_reload_for_other_writer(self):
        self.post(toggle="cal")
        html = self.manager.get(f"{self.base}/screens").content.decode()
        self.assertIn('aria-label="칼로리 · 영양 켜기"', html)


class PrototypeParityQa(QaBase):
    """prototype/assets/meal.js를 직접 읽어 비교한다."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(REPO, "prototype/assets/meal.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_hygiene_and_slogan_text_same_as_prototype(self):
        for h in content.HYGIENE:
            self.assertIn(f"{h['key']}: {{ name: '{h['name']}', title: '{h['title']}', tag: '{h['tag']}'", self.js)
            for a, b in h["items"]:
                self.assertIn(f"['{a}', '{b}']", self.js)
        for s in content.SLOGANS:
            self.assertIn(f"'{s}'", self.js)
        self.assertEqual(self.js.count("items: ["), len(content.HYGIENE))

    def test_rotation_sequence_from_node(self):
        """TZ=Asia/Seoul node로 meal.js를 돌려 얻은 값(2026-11-01부터 8일). 730일 전체 비교는 검증 때 한 번 돌렸다."""
        hygiene = ["teeth", "safe", "wash", "cough", "food", "teeth", "safe", "wash"]
        slogans = ["먹을 만큼", "천천히", "우유", "음식을", "편식", "골고루", "채소", "먹을 만큼"]
        for i, (h, s) in enumerate(zip(hygiene, slogans, strict=True)):
            day = date(2026, 11, 1) + timedelta(days=i)
            self.assertEqual(content.hygiene_of(day)["key"], h)
            self.assertTrue(content.slogan_of(day).startswith(s))

    def test_screen_keys_order_lock_and_seconds(self):
        self.assertEqual(content.SCREEN_KEYS, ["menu", "allergy", "cal", "origin", "hygiene", "eat", "photo", "week"])
        self.assertEqual({s["key"]: s["sec"] for s in content.SCREENS if s["sec"] != 7}, {"origin": 12, "week": 12})
        self.assertIn("{ key: 'photo', name: '오늘의 식사', what: '그날 식판 사진 (당일 찍은 사진)', src: 'me', lock: true }", self.js)

    def test_no_old_name_or_staff_words_anywhere_in_new_code(self):
        base = os.path.join(REPO, "app/meals")
        paths = [os.path.join(REPO, "web/static/js/meals.js"), os.path.join(base, "content.py")]
        for root, _, names in os.walk(os.path.join(base, "templates")):
            paths += [os.path.join(root, n) for n in names]
        for path in paths:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for word in ("티로드", "T-Road", "시연용", "영양 선생님", "영양사"):
                self.assertNotIn(word, text, path)
        for url in (self.base, f"{self.base}/screens"):
            html = self.writer.get(url).content.decode()
            self.assertNotIn("티로드", html, url)
            self.assertIn("고티처", html, url)


class PurgeQa(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)

    def make(self, day):
        p = MealPhoto.objects.create(space=self.hub, date=day, stored=4)
        p.photo.save("x.jpg", ContentFile(b"data"))
        return p

    def test_command_30_days_kept_31_removed_with_file(self):
        today = timezone.localdate()
        keep = self.make(today - timedelta(days=30))
        drop = self.make(today - timedelta(days=31))
        name = drop.photo.name
        out = io.StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("purge", "--dry-run", stdout=out)
        self.assertIn("지난 식판 사진 1건", out.getvalue())
        self.assertTrue(MealPhoto.objects.filter(pk=drop.pk).exists())
        out = io.StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("purge", stdout=out)
        self.assertIn("지난 식판 사진 1건", out.getvalue())
        self.assertTrue(MealPhoto.objects.filter(pk=keep.pk).exists())
        self.assertTrue(default_storage.exists(keep.photo.name))
        self.assertFalse(MealPhoto.objects.filter(pk=drop.pk).exists())
        self.assertFalse(default_storage.exists(name))

    def test_boundary_is_korean_midnight_not_utc(self):
        day = date(2026, 10, 3)
        self.make(day)
        before = datetime(2026, 11, 2, 14, 59, tzinfo=dt_timezone.utc)  # KST 11/2 23:59 → 30일째
        after = datetime(2026, 11, 2, 15, 0, tzinfo=dt_timezone.utc)  # KST 11/3 00:00 → 31일째
        self.assertEqual(purge(before, dry_run=True)["meal_photos"], 0)
        self.assertEqual(purge(after, dry_run=True)["meal_photos"], 1)

    def test_other_school_photos_also_expire_but_only_old(self):
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        today = timezone.localdate()
        MealPhoto.objects.create(space=far_hub, date=today, stored=0)
        MealPhoto.objects.create(space=self.hub, date=today - timedelta(days=40), stored=0)
        purge(timezone.now())
        self.assertEqual(list(MealPhoto.objects.values_list("space_id", flat=True)), [far_hub.pk])

    def test_space_deletion_removes_files(self):
        p = self.make(timezone.localdate())
        name = p.photo.name
        with self.captureOnCommitCallbacks(execute=True):
            self.hub.delete()
        self.assertFalse(default_storage.exists(name))


@skipUnlessDBFeature("has_select_for_update")
class ConcurrencyQa(TransactionTestCase):
    """PostgreSQL에서만 의미가 있다(sqlite는 건너뜀): 실제 스레드 + 실제 커밋."""

    N = 6

    def setUp(self):
        cache.clear()
        caches["ratelimit"].clear()
        import shutil
        import tempfile

        from django.contrib.auth import get_user_model
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)
        for target, value in (("meals.views.timezone.localdate", TUE), ("spaces.neis.meals", MENU), ("spaces.neis.class_names", ["1", "2"])):
            p = mock.patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        g = Space.objects.create(school=school, grade=4, name="4학년")
        U = get_user_model()
        self.users = [U.objects.create(username=f"google:{i}", first_name=f"담당{i}") for i in range(self.N)]
        self.members = [Member.objects.create(space=g, user=u, name=f"담당{i}", cls=f"4-{i}") for i, u in enumerate(self.users)]
        self.hub = open_school(school, UNTIL, self.members[0])
        Member.objects.in_space(self.hub).update(can_meal=True)
        self.base = f"/s/{self.hub.token}/meals"

    def run_all(self, jobs):
        results, gate = [None] * len(jobs), threading.Barrier(len(jobs))

        def work(i, job):
            try:
                c = Client(raise_request_exception=False)
                c.force_login(self.users[i % self.N])
                gate.wait()
                results[i] = job(c).status_code
            except Exception as e:
                results[i] = repr(e)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=work, args=(i, j)) for i, j in enumerate(jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def files(self):
        d = f"meal/{self.hub.pk}"
        return [f"{d}/{n}" for n in default_storage.listdir(d)[1]] if default_storage.exists(d) else []

    def upload(self, color):
        data = jpeg_bytes(color=color)
        return lambda c: c.post(f"{self.base}/20261103/upload", {"photo": up(data)})

    def test_vendor_is_postgres(self):
        self.assertEqual(connection.vendor, "postgresql")

    def test_first_upload_same_day_race_one_row_one_file(self):
        for _ in range(5):
            MealPhoto.objects.all().delete()
            got = self.run_all([self.upload((i * 30, 0, 0)) for i in range(self.N)])
            self.assertEqual(got, [200] * self.N)
            row = MealPhoto.objects.in_space(self.hub).get(date=TUE)
            self.assertEqual(self.files(), [row.photo.name])
            self.assertEqual(row.stored, default_storage.size(row.photo.name))

    def test_upload_and_delete_race_never_500(self):
        statuses = []
        for _ in range(25):
            caches["ratelimit"].clear()
            self.client.force_login(self.users[0])
            self.client.post(f"{self.base}/20261103/upload", {"photo": up(jpeg_bytes())})
            got = self.run_all([self.upload((1, 2, 3)), lambda c: c.post(f"{self.base}/20261103/delete"), self.upload((3, 2, 1))])
            statuses += got
            rows = MealPhoto.objects.in_space(self.hub).filter(date=TUE)
            self.assertLessEqual(rows.count(), 1)
            self.assertEqual(sorted(self.files()), sorted(r.photo.name for r in rows))
        self.assertNotIn(500, statuses, statuses)

    def test_screen_toggles_are_all_kept(self):
        keys = ["menu", "allergy", "cal", "origin", "hygiene", "eat"]
        got = self.run_all([(lambda c, k=k: c.post(f"{self.base}/screens", {"toggle": k})) for k in keys])
        self.assertEqual(got, [200] * len(keys))
        state = {s["key"]: s["on"] for s in MealSettings.screens_of(self.hub)}
        self.assertEqual([k for k, on in state.items() if on], ["photo", "week"])
        self.assertEqual(MealSettings.objects.in_space(self.hub).count(), 1)

    def test_first_screen_settings_race_one_row(self):
        got = self.run_all([(lambda c: c.post(f"{self.base}/screens", {"up": "cal"})) for _ in range(self.N)])
        self.assertEqual(got, [200] * self.N)
        self.assertEqual(MealSettings.objects.in_space(self.hub).count(), 1)
