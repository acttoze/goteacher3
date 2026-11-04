"""M1 급식 입력: 권한·격리·사진·날짜 경고·NEIS 실패·보관·TV 화면 설정."""
from datetime import date, timedelta
from io import BytesIO
from unittest import mock

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import new_guest
from hwpfiles.models import SourceFile
from hwpfiles.retention import orphan_files, purge
from repairs.tests import gps_exif, jpeg_bytes, png_bytes, png_header_only, up
from spaces import neis
from spaces.merge import open_school
from spaces.models import Member, MemberLog, School
from spaces.test_merge import UNTIL, MergeBase

from . import content, views
from .models import MealPhoto, MealSettings

TUE = date(2026, 11, 3)  # 화요일
WEEK = ("20261102", "20261103", "20261104", "20261105", "20261106")


def meal(ymd, *dishes, kcal=650):
    return {"ymd": ymd, "dishes": [{"name": n, "allergy": a, "self": False} for n, a in dishes], "kcal": kcal, "count": 400,
            "nutr": [{"name": "단백질", "unit": "g", "value": 20.0}], "origin": [["쌀", "국내산"], ["돼지고기", "국내산"]]}


MENU = [meal("20261103", ("쌀밥", []), ("돈육김치찌개", [5, 9, 10]), ("깍두기", [9])), meal("20261104", ("카레라이스", [2]))]


def jpeg_taken(day):
    """EXIF 촬영일이 적힌 JPEG."""
    exif = Image.Exif()
    exif.get_ifd(views.EXIF_IFD)[views.DATE_ORIGINAL] = f"{day:%Y:%m:%d} 09:30:00"
    out = BytesIO()
    Image.new("RGB", (64, 48), (200, 40, 40)).save(out, "JPEG", exif=exif)
    return out.getvalue()


class Base(MergeBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        for target, value in (("meals.views.timezone.localdate", TUE), ("spaces.neis.meals", MENU)):
            p = mock.patch(target, return_value=value)
            setattr(self, target.rsplit(".", 1)[1], p.start())
            self.addCleanup(p.stop)
        self.hub = open_school(self.school, UNTIL, self.m4)  # m4 관리자, m4b·m5 일반
        for m in (self.m4, self.m4b, self.m5):
            m.refresh_from_db()
        self.base = f"/s/{self.hub.token}/meals"
        self.manager, self.teacher, self.writer = self.as_(self.u4), self.as_(self.u4b), self.as_(self.u5)
        Member.objects.filter(pk=self.m5.pk).update(can_meal=True)
        self.used0 = SourceFile.used_bytes(self.hub)  # 합칠 때 옮겨 온 한글 파일

    def as_(self, user):
        c = Client()
        c.force_login(user)
        return c

    def post_photo(self, data=None, ymd="20261103", client=None, name="p.jpg"):
        return (client or self.writer).post(f"{self.base}/{ymd}/upload", {"photo": up(jpeg_bytes() if data is None else data, name)})

    def row(self, ymd=TUE):
        return MealPhoto.objects.in_space(self.hub).get(date=ymd)

    def expire(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=TUE - timedelta(days=1))


class AccessTests(Base):
    def test_teacher_403_even_as_meal_group(self):
        Member.objects.filter(pk=self.m4b.pk).update(group=Member.Group.MEAL)
        for url in (self.base, f"{self.base}/screens"):
            self.assertEqual(self.teacher.get(url).status_code, 403)
        self.assertEqual(self.post_photo(client=self.teacher).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/screens", {"toggle": "menu"}).status_code, 403)
        self.assertFalse(MealPhoto.objects.exists())

    def test_teacher_cannot_delete(self):
        self.post_photo()
        self.assertEqual(self.teacher.post(f"{self.base}/20261103/delete").status_code, 403)
        self.assertTrue(MealPhoto.objects.exists())

    def test_designated_and_manager_allowed(self):
        for c in (self.writer, self.manager):
            self.assertEqual(c.get(self.base).status_code, 200)
            self.assertEqual(c.get(f"{self.base}/screens").status_code, 200)
        self.assertEqual(self.post_photo().status_code, 200)
        self.assertEqual(self.post_photo(client=self.manager).status_code, 200)

    def test_other_school_and_grade_space_404(self):
        far = self.as_(self.far_user)
        self.post_photo()
        for url in (self.base, f"{self.base}/screens", f"{self.base}/20261103/photo"):
            self.assertEqual(far.get(url).status_code, 404)
        self.assertEqual(far.post(f"{self.base}/20261103/upload").status_code, 404)
        self.assertEqual(far.post(f"{self.base}/20261103/delete").status_code, 404)
        # 동학년 공간에는 이 주소가 없다
        grade = f"/s/{self.far.token}/meals"
        self.assertEqual(far.get(grade).status_code, 404)
        self.assertEqual(far.get(f"{grade}/screens").status_code, 404)
        self.assertEqual(far.post(f"{grade}/20261103/upload").status_code, 404)
        self.assertEqual(far.get(f"{grade}/20261103/photo").status_code, 404)

    def test_same_date_in_other_space_is_not_visible(self):
        far_school = open_school(self.far_school, UNTIL, self.far_member)
        MealPhoto.objects.create(space=far_school, date=TUE, stored=0)
        self.assertEqual(self.writer.get(f"{self.base}/20261103/photo").status_code, 404)
        self.assertEqual(self.writer.post(f"{self.base}/20261103/delete").status_code, 404)
        self.assertTrue(MealPhoto.objects.in_space(far_school).exists())

    def test_anonymous_goes_to_login(self):
        self.assertEqual(Client().get(self.base).status_code, 302)
        self.assertEqual(Client().post(f"{self.base}/20261103/upload").status_code, 302)

    def test_expired_school_reads_but_cannot_write(self):
        self.post_photo()
        self.expire()
        r = self.writer.get(self.base)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "/upload")
        self.assertEqual(self.writer.get(f"{self.base}/screens").status_code, 200)
        self.assertEqual(self.writer.get(f"{self.base}/20261103/photo").status_code, 200)
        self.assertEqual(self.post_photo(ymd="20261104").status_code, 403)
        self.assertEqual(self.writer.post(f"{self.base}/screens", {"toggle": "menu"}).status_code, 403)
        self.assertEqual(self.manager.post(f"/s/{self.hub.token}/school/members/{self.m4b.key}/meal", {"on": "1"}).status_code, 403)
        # 지우기는 개인정보 조치라 받는다(수리 사진과 같다)
        self.assertEqual(self.writer.post(f"{self.base}/20261103/delete").status_code, 200)

    def test_photo_download_for_members_only(self):
        self.post_photo()
        url = f"{self.base}/20261103/photo"
        r = self.teacher.get(url)  # 일반 회원은 화면은 못 봐도 사진은 본다
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/jpeg"))
        self.assertIn("private", r["Cache-Control"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        b"".join(r.streaming_content)
        self.assertEqual(Client().get(url).status_code, 302)

    def test_bad_date_404(self):
        self.assertEqual(self.writer.get(f"{self.base}/2026-13-45/photo").status_code, 404)
        self.assertEqual(self.writer.post(f"{self.base}/nope/upload").status_code, 404)
        self.assertEqual(self.writer.post(f"{self.base}/20261304/delete").status_code, 404)


class MenuLinkTests(Base):
    def test_menu_only_for_manager_and_designated_in_school(self):
        link = f'href="{self.base}"'
        today = f"/s/{self.hub.token}/today"
        self.assertContains(self.manager.get(today), link)
        self.assertContains(self.writer.get(today), link)
        self.assertNotContains(self.teacher.get(today), link)
        self.assertNotContains(self.as_(self.far_user).get(f"/s/{self.far.token}/today"), "/meals")


class DesignateTests(Base):
    def url(self, m):
        return f"/s/{self.hub.token}/school/members/{m.key}/meal"

    def test_manager_sets_and_clears_with_log(self):
        self.assertEqual(self.manager.post(self.url(self.m4b), {"on": "1"}).status_code, 302)
        self.m4b.refresh_from_db()
        self.assertTrue(self.m4b.can_meal)
        self.assertEqual(self.teacher.get(self.base).status_code, 200)
        self.assertTrue(MemberLog.objects.in_space(self.hub).filter(name=self.m4b.name, after="급식 담당").exists())
        self.manager.post(self.url(self.m4b), {"on": "0"})
        self.assertEqual(self.teacher.get(self.base).status_code, 403)

    def test_only_manager(self):
        self.assertEqual(self.writer.post(self.url(self.m4b), {"on": "1"}).status_code, 403)

    def test_guest_refused(self):
        guest = Member.objects.create(space=self.hub, user=new_guest("손님"), name="손님", cls="4-3")
        self.assertEqual(self.manager.post(self.url(guest), {"on": "1"}).status_code, 400)
        guest.refresh_from_db()
        self.assertFalse(guest.can_meal)

    def test_other_space_member_404(self):
        self.assertEqual(self.manager.post(self.url(self.far_member), {"on": "1"}).status_code, 404)

    def test_school_page_has_designate_button_and_pill(self):
        r = self.manager.get(f"/s/{self.hub.token}/school")
        self.assertContains(r, "급식 담당자로 지정")
        self.assertContains(r, "급식 담당</span>")


class PhotoTests(Base):
    def test_gps_and_exif_removed(self):
        raw = BytesIO()
        Image.new("RGB", (64, 48), (1, 2, 3)).save(raw, "JPEG", exif=gps_exif())
        self.assertIn(b"SecretCamera", raw.getvalue())
        self.post_photo(raw.getvalue())
        with default_storage.open(self.row().photo.name) as f:
            kept = f.read()
        self.assertNotIn(b"SecretCamera", kept)
        self.assertFalse(Image.open(BytesIO(kept)).getexif())

    def test_stored_name_is_token_not_upload_name(self):
        self.post_photo(name="김영양_식판.jpg")
        name = self.row().photo.name
        self.assertRegex(name, rf"^meal/{self.hub.pk}/[\w-]+\.jpg$")
        self.assertNotIn("김영양", name)

    def assertRefused(self, data, name="p.jpg"):
        r = self.post_photo(data, name=name)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MealPhoto.objects.exists())
        return r

    def test_only_name_jpg_refused(self):
        self.assertContains(self.assertRefused(b"not an image at all", "real.jpg"), "JPG·PNG", status_code=400)
        gif = BytesIO()
        Image.new("RGB", (8, 8)).save(gif, "GIF")
        self.assertRefused(gif.getvalue(), "real.jpg")

    def test_broken_file_refused(self):
        self.assertRefused(jpeg_bytes()[:30])

    def test_pixel_bomb_refused(self):
        self.assertContains(self.assertRefused(png_header_only(30000, 30000), "b.png"), "화소", status_code=400)

    def test_scan_bomb_refused(self):
        self.assertRefused(jpeg_bytes() + b"\xff\xda" * 70)

    def test_too_big_refused(self):
        self.assertContains(self.assertRefused(b"\xff\xd8" + b"0" * (8 * 1024 * 1024)), "8MB", status_code=400)

    def test_png_ok_and_saved_as_jpeg(self):
        self.assertEqual(self.post_photo(png_bytes(), name="a.png").status_code, 200)
        self.assertEqual(Image.open(self.row().photo.path).format, "JPEG")

    def test_missing_file_400(self):
        self.assertEqual(self.writer.post(f"{self.base}/20261103/upload").status_code, 400)

    def test_upload_rate_limited(self):
        with mock.patch("meals.views.allow", return_value=False):
            r = self.post_photo(jpeg_bytes())
        self.assertEqual(r.status_code, 429)
        self.assertFalse(MealPhoto.objects.exists())

    def test_quota_counts_only_growth_on_replace(self):
        self.post_photo()
        used = SourceFile.used_bytes(self.hub)
        self.assertEqual(used, self.used0 + self.row().stored)
        with override_settings(HWP_SCHOOL_QUOTA=used):  # 꽉 찬 공간
            self.assertEqual(self.post_photo(ymd="20261104").status_code, 400)  # 새 칸은 못 올린다
            self.assertEqual(self.post_photo(jpeg_bytes()).status_code, 200)  # 같은 크기로 바꾸기는 된다

    def test_replace_same_day_removes_old_file_and_keeps_quota(self):
        self.post_photo(jpeg_bytes(size=(640, 480)))
        old = self.row()
        old_name = old.photo.name
        with self.captureOnCommitCallbacks(execute=True):
            self.post_photo(jpeg_bytes(size=(64, 48)))
        new = self.row()
        self.assertEqual(MealPhoto.objects.in_space(self.hub).count(), 1)
        self.assertNotEqual(new.photo.name, old_name)
        self.assertFalse(default_storage.exists(old_name))
        self.assertTrue(default_storage.exists(new.photo.name))
        self.assertEqual(new.stored, default_storage.size(new.photo.name))
        self.assertEqual(SourceFile.used_bytes(self.hub), self.used0 + new.stored)
        self.assertEqual(new.by_id, self.m5.pk)
        self.assertGreaterEqual(new.created_at, old.created_at)

    def test_delete_removes_file_and_quota(self):
        self.post_photo()
        name = self.row().photo.name
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.writer.post(f"{self.base}/20261103/delete").status_code, 200)
        self.assertFalse(MealPhoto.objects.exists())
        self.assertFalse(default_storage.exists(name))
        self.assertEqual(SourceFile.used_bytes(self.hub), self.used0)

    def test_htmx_returns_fragment_full_otherwise(self):
        r = self.writer.post(f"{self.base}/20261103/upload", {"photo": up(jpeg_bytes())}, headers={"HX-Request": "true"})
        self.assertContains(r, 'id="mealMain"')
        self.assertNotContains(r, "<aside")
        self.assertContains(self.writer.get(self.base), "<aside")


class DateWarningTests(Base):
    def test_today_cell_with_same_day_exif_no_warning(self):
        r = self.post_photo(jpeg_taken(TUE))
        self.assertNotContains(r, "칸이 맞는지")
        self.assertNotContains(r, "다른 날 찍은")
        self.assertContains(r, "사진을 올렸어요")

    def test_no_exif_no_warning(self):
        self.assertNotContains(self.post_photo(), "다른 날 찍은")

    def test_other_cell_warns_but_records_that_date(self):
        r = self.post_photo(jpeg_taken(date(2026, 11, 4)), ymd="20261104")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "칸이 맞는지")
        self.assertNotContains(r, "다른 날 찍은")
        self.assertEqual(MealPhoto.objects.in_space(self.hub).get().date, date(2026, 11, 4))

    def test_exif_other_day_warns_not_blocks(self):
        r = self.post_photo(jpeg_taken(date(2026, 11, 2)))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "다른 날 찍은 사진 같아요")
        self.assertEqual(self.row().date, TUE)

    def test_outside_this_week_refused(self):
        self.assertEqual(self.post_photo(ymd="20261110").status_code, 400)
        self.assertEqual(self.post_photo(ymd="20261108").status_code, 400)  # 일요일
        self.assertFalse(MealPhoto.objects.exists())

    def test_broken_exif_is_ignored(self):
        exif = Image.Exif()
        exif.get_ifd(views.EXIF_IFD)[views.DATE_ORIGINAL] = "0000:00:00 00:00:00"
        out = BytesIO()
        Image.new("RGB", (32, 32)).save(out, "JPEG", exif=exif)
        r = self.post_photo(out.getvalue())
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "다른 날 찍은")


class WeekPageTests(Base):
    def test_five_cells_today_highlighted_and_needs_photo(self):
        r = self.writer.get(self.base)
        self.assertEqual(r.content.decode().count('class="mday'), 5)
        self.assertContains(r, 'class="mday today need"')
        self.assertContains(r, "오늘 사진 아직")
        self.assertContains(r, "돈육김치찌개")
        self.assertContains(r, "650kcal · 알레르기 3종")
        self.assertContains(r, "영양 1종 · 원산지 2종")
        self.assertContains(r, "화 11/3")

    def test_after_upload_no_longer_needs(self):
        self.post_photo()
        r = self.writer.get(self.base)
        self.assertNotContains(r, " need")
        self.assertContains(r, "오늘 사진 올림")
        self.assertContains(r, f'{self.base}/20261103/photo?v=')

    def test_no_meal_is_not_failure(self):
        self.meals.return_value = []
        r = self.writer.get(self.base)
        self.assertContains(r, "급식 없음")
        self.assertNotContains(r, "불러오지 못했어요")
        self.assertNotContains(r, " need")  # 방학·휴업일엔 재촉하지 않는다

    def test_neis_failure_shows_retry_and_still_allows_upload(self):
        self.meals.return_value = neis.Failed()
        r = self.writer.get(self.base)
        self.assertContains(r, "불러오지 못했어요")
        self.assertContains(r, "다시 시도")
        self.assertNotContains(r, "급식 없음")
        self.assertContains(r, 'class="mday today need"')  # 사진은 규정이라 NEIS가 멈춰도 올린다
        self.assertEqual(self.post_photo().status_code, 200)

    def test_retry_forgets_failed_cache_only(self):
        self.meals.return_value = neis.Failed()
        with mock.patch("spaces.neis.forget_failed_meals") as forget:
            self.writer.get(self.base)
            forget.assert_not_called()
            self.writer.get(self.base + "?retry=1")
        forget.assert_called_once_with("J10", "7531234", "20261102", "20261106")

    def test_weekend_shows_next_week(self):
        self.localdate.return_value = date(2026, 11, 7)  # 토요일
        r = self.writer.get(self.base)
        self.assertContains(r, "월 11/9")
        self.assertContains(r, "주말이라")
        self.assertNotContains(r, "mday today")

    def test_no_old_or_school_wording(self):
        r = self.writer.get(self.base).content.decode()
        for word in ("티로드", "영양사"):
            self.assertNotIn(word, r)


class NeisForgetTests(SimpleTestCase):
    def test_forget_only_removes_failure(self):
        span = ("J10", "7531234", "20261102", "20261106")
        key = neis._cache_key("mealServiceDietInfo", neis._meal_params(*span))
        cache.set(key, ([{"MLSV_YMD": "20261103"}], 1), 60)
        neis.forget_failed_meals(*span)
        self.assertIsNotNone(cache.get(key))
        cache.set(key, (neis.Failed(), 0), 60)
        neis.forget_failed_meals(*span)
        self.assertIsNone(cache.get(key))


class RotationTests(Base):
    def test_same_day_same_content_and_changes_daily(self):
        self.assertEqual(content.hygiene_of(TUE), content.hygiene_of(TUE))
        week = [content.hygiene_of(TUE + timedelta(days=i))["key"] for i in range(5)]
        self.assertEqual(sorted(week), sorted(h["key"] for h in content.HYGIENE))
        slogans = {content.slogan_of(TUE + timedelta(days=i)) for i in range(7)}
        self.assertEqual(slogans, set(content.SLOGANS))

    def test_matches_prototype_day_number(self):
        # meal.js: dayNo = Math.round(자정 / 864e5), 2026-11-03 KST = 20760일 → keys[20760 % 5] = wash, SLOGANS[20760 % 7]
        self.assertEqual((TUE - content.EPOCH).days, 20760)
        self.assertEqual(content.hygiene_of(TUE)["key"], "wash")
        self.assertEqual(content.slogan_of(TUE), content.SLOGANS[20760 % 7])

    def test_screen_page_shows_today_rotation(self):
        r = self.writer.get(f"{self.base}/screens")
        self.assertContains(r, content.hygiene_of(TUE)["title"])
        self.assertContains(r, content.slogan_of(TUE))


class ScreensTests(Base):
    def post(self, **data):
        return self.writer.post(f"{self.base}/screens", data)

    def keys(self):
        return [s["key"] for s in MealSettings.screens_of(self.hub)]

    def test_default_all_on_in_prototype_order(self):
        items = MealSettings.screens_of(self.hub)
        self.assertEqual([s["key"] for s in items], content.SCREEN_KEYS)
        self.assertTrue(all(s["on"] for s in items))
        self.assertFalse(MealSettings.objects.exists())

    def test_toggle_and_order_saved(self):
        self.assertEqual(self.post(toggle="allergy").status_code, 200)
        self.assertEqual(self.post(up="cal").status_code, 200)
        self.assertEqual(self.post(down="menu").status_code, 200)
        items = MealSettings.screens_of(self.hub)
        self.assertEqual([s["key"] for s in items][:3], ["cal", "menu", "allergy"])
        self.assertFalse(next(s for s in items if s["key"] == "allergy")["on"])
        self.post(toggle="allergy")
        self.assertTrue(next(s for s in MealSettings.screens_of(self.hub) if s["key"] == "allergy")["on"])

    def test_ends_do_not_wrap(self):
        self.post(up=content.SCREEN_KEYS[0])
        self.post(down=content.SCREEN_KEYS[-1])
        self.assertEqual(self.keys(), content.SCREEN_KEYS)

    def test_photo_screen_cannot_be_turned_off(self):
        self.assertEqual(self.post(toggle=content.PHOTO).status_code, 400)
        self.assertNotContains(self.writer.get(f"{self.base}/screens"), f'name="toggle" value="{content.PHOTO}"')
        self.assertContains(self.writer.get(f"{self.base}/screens"), "항상 켜짐")
        # 저장값이 어떻게 되어도 켜져 있다
        got = content.screens([{"key": content.PHOTO, "on": False}, {"key": "nope", "on": True}])
        self.assertTrue(next(s for s in got if s["key"] == content.PHOTO)["on"])
        self.assertEqual(sorted(s["key"] for s in got), sorted(content.SCREEN_KEYS))

    def test_photo_screen_can_move(self):
        self.post(up=content.PHOTO)
        self.assertLess(self.keys().index(content.PHOTO), content.SCREEN_KEYS.index(content.PHOTO))

    def test_bad_key_400(self):
        self.assertEqual(self.post(toggle="nope").status_code, 400)
        self.assertEqual(self.post().status_code, 400)

    def test_settings_isolated_between_schools(self):
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        self.post(toggle="menu")
        self.assertTrue(next(s for s in MealSettings.screens_of(far_hub) if s["key"] == "menu")["on"])


class RetentionTests(Base):
    def make(self, day):
        p = MealPhoto.objects.create(space=self.hub, date=day, stored=4)
        p.photo.save("x.jpg", ContentFile(b"data"))
        return p

    def test_purge_after_30_days(self):
        today = timezone.localdate()
        keep, drop = self.make(today - timedelta(days=30)), self.make(today - timedelta(days=31))
        name = drop.photo.name
        now = timezone.now()
        dry = purge(now, dry_run=True)
        self.assertEqual(dry["meal_photos"], 1)
        with self.captureOnCommitCallbacks(execute=True):
            n = purge(now)
        self.assertEqual(n["meal_photos"], 1)
        self.assertTrue(MealPhoto.objects.filter(pk=keep.pk).exists())
        self.assertFalse(MealPhoto.objects.filter(pk=drop.pk).exists())
        self.assertFalse(default_storage.exists(name))

    def test_orphan_in_meal_dir_collected_and_referenced_kept(self):
        kept = self.make(TUE)
        default_storage.save(f"meal/{self.hub.pk}/orphan.jpg", ContentFile(b"x"))
        found = orphan_files(timezone.now() + timedelta(hours=2))
        self.assertIn(f"meal/{self.hub.pk}/orphan.jpg", found)
        self.assertNotIn(kept.photo.name, found)

    def test_stored_mismatch_fixed(self):
        p = self.make(TUE)
        MealPhoto.objects.filter(pk=p.pk).update(stored=999)
        purge(timezone.now())
        p.refresh_from_db()
        self.assertEqual(p.stored, 4)


class NeisParseTests(SimpleTestCase):
    def test_meals_carry_nutr_and_origin(self):
        row = {"MLSV_YMD": "20261103", "DDISH_NM": "쌀밥", "CAL_INFO": "650 Kcal", "NTR_INFO": "칼슘(mg) : 200.5", "ORPLC_INFO": "쌀 : 국내산"}
        with mock.patch("spaces.neis._get", return_value=([row], 1)):
            got = neis.meals("J10", "7531234", "20261103", "20261103")[0]
        self.assertEqual(got["nutr"], [{"name": "칼슘", "unit": "mg", "value": 200.5}])
        self.assertEqual(got["origin"], [["쌀", "국내산"]])

    def test_nutr_and_origin(self):
        self.assertEqual(neis._nutr("탄수화물(g) : 90.1<br/>단백질(g) : 21<br/>이상한 줄"),
                         [{"name": "탄수화물", "unit": "g", "value": 90.1}, {"name": "단백질", "unit": "g", "value": 21.0}])
        self.assertEqual(neis._origin("쌀 : 국내산<br/>쇠고기(종류) : 호주산<br/>비고 : 없음<br/>빈칸 :"),
                         [["쌀", "국내산"], ["쇠고기", "호주산"]])
        self.assertEqual((neis._nutr(None), neis._origin("")), ([], []))
