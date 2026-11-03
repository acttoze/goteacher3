"""K1 독립 검증(gt-qa): repairs/tests.py에 없는 경계만. 사진은 실제 GPS·XMP·회전이 든 바이트로 본다."""
import io
import os
import time
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image, ImageOps, TiffImagePlugin

from collect import push
from collect.models import Notification, PushSubscription
from hwpfiles.models import SourceFile
from notices.models import Notice
from spaces.models import Member, Space

from . import photo, views
from .models import Repair
from .tests import Base, jpeg_bytes, up

KIND = Notification.Kind
R, G, B, Y = (220, 20, 20), (20, 200, 20), (20, 20, 220), (230, 220, 20)
INVERSE = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
           5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_90, 7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_270}
XMP = (b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><rdf:Description exif:GPSLatitude=\"37,30.5N\" "
       b"exif:GPSLongitude=\"127,0.1E\"/></x:xmpmeta>")


def quadrants(size):
    w, h = size
    im = Image.new("RGB", size, R)
    im.paste(G, (w // 2, 0, w, h // 2))
    im.paste(B, (0, h // 2, w // 2, h))
    im.paste(Y, (w // 2, h // 2, w, h))
    return im


def real_exif(orientation=None):
    """폰 사진처럼 카메라·촬영 시각·GPS IFD가 모두 든 EXIF."""
    r = TiffImagePlugin.IFDRational
    exif = Image.Exif()
    exif[0x010F] = "SECRET-MAKER"
    exif[0x0110] = "SECRET-MODEL"
    exif[0x0132] = "2026:11:03 10:11:12"
    if orientation:
        exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (r(37), r(30), r(1234, 100))
    gps[3] = "E"
    gps[4] = (r(127), r(1), r(5678, 100))
    return exif


def phone_jpeg(upright_size, orientation, xmp=True):
    stored = quadrants(upright_size)
    if orientation in INVERSE:
        stored = stored.transpose(INVERSE[orientation])
    out = io.BytesIO()
    stored.save(out, "JPEG", quality=92, exif=real_exif(orientation), comment=b"SECRET-COMMENT",
                icc_profile=b"SECRET-ICC" * 20, **({"xmp": XMP} if xmp else {}))
    return out.getvalue()


def jpeg_segments(data):
    """SOS 전까지 JPEG 마커 목록."""
    i, seen = 2, []
    while i < len(data):
        marker = data[i + 1]
        seen.append(marker)
        if marker == 0xDA:
            break
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return seen


def color_is(px, want):
    return all(abs(a - b) < 60 for a, b in zip(px[:3], want))


class QaBase(Base):
    def saved(self, raw, name="IMG_0001.jpg"):
        Repair.objects.in_space(self.hub).delete()
        res = self.request(photo=up(raw, name))
        self.assertEqual(res.status_code, 302)
        r = Repair.objects.in_space(self.hub).get()
        with r.photo.open("rb") as fh:
            return r, fh.read()


class RealPhotoTests(QaBase):
    def test_input_really_has_gps_and_xmp_then_saved_file_has_none(self):
        raw = phone_jpeg((800, 600), 1)
        with Image.open(io.BytesIO(raw)) as src:
            gps = src.getexif().get_ifd(0x8825)
            self.assertEqual(gps[1], "N")   # 입력 전제: GPS가 정말 들어 있다
            self.assertEqual(float(gps[2][0]), 37.0)
        for needle in (b"SECRET-MAKER", b"SECRET-COMMENT", b"SECRET-ICC", b"GPSLatitude"):
            self.assertIn(needle, raw)
        _, stored = self.saved(raw)
        for needle in (b"SECRET-MAKER", b"SECRET-MODEL", b"SECRET-COMMENT", b"SECRET-ICC", b"GPSLatitude", b"Exif", b"xmpmeta", b"2026:11:03"):
            self.assertNotIn(needle, stored)
        markers = jpeg_segments(stored)
        self.assertNotIn(0xE1, markers)  # APP1(EXIF·XMP)
        self.assertNotIn(0xE2, markers)  # APP2(ICC·MPF)
        self.assertNotIn(0xED, markers)  # APP13(IPTC)
        self.assertNotIn(0xFE, markers)  # COM
        with Image.open(io.BytesIO(stored)) as out:
            self.assertEqual(dict(out.getexif().get_ifd(0x8825)), {})
            self.assertEqual(len(out.getexif()), 0)

    def test_every_orientation_ends_upright_small(self):
        for o in range(1, 9):
            _, stored = self.saved(phone_jpeg((400, 200), o))
            with Image.open(io.BytesIO(stored)) as im:
                self.assertEqual(im.size, (400, 200), o)
                self.assertEqual(im.getexif().get(0x0112), None)
                for xy, want in (((60, 40), R), ((340, 40), G), ((60, 160), B), ((340, 160), Y)):
                    self.assertTrue(color_is(im.getpixel(xy), want), f"orientation={o} {xy} {im.getpixel(xy)}")

    def test_phone_portrait_4032x3024_stands_up_after_draft_and_resize(self):
        """가로로 저장되고 EXIF 6인 전형적인 폰 세로 사진. draft·thumbnail을 모두 지난다."""
        for o in (3, 5, 6, 7, 8):
            size = (3024, 4032)  # 사람이 보는 방향
            _, stored = self.saved(phone_jpeg(size, o, xmp=False))
            with Image.open(io.BytesIO(stored)) as im:
                self.assertEqual(im.size, (1200, 1600), o)
                for xy, want in (((200, 300), R), ((1000, 300), G), ((200, 1300), B), ((1000, 1300), Y)):
                    self.assertTrue(color_is(im.getpixel(xy), want), f"orientation={o} {xy} {im.getpixel(xy)}")

    def test_png_exif_orientation_applied_and_removed(self):
        out = io.BytesIO()
        quadrants((400, 200)).transpose(INVERSE[6]).save(out, "PNG", exif=real_exif(6))
        with Image.open(io.BytesIO(out.getvalue())) as src:
            self.assertEqual(src.getexif().get(0x0112), 6)
        _, stored = self.saved(out.getvalue(), "a.png")
        self.assertNotIn(b"SECRET-MAKER", stored)
        with Image.open(io.BytesIO(stored)) as im:
            self.assertEqual(im.size, (400, 200))
            self.assertTrue(color_is(im.getpixel((60, 40)), R))

    def test_junk_after_jpeg_and_polyglot_dropped(self):
        raw = jpeg_bytes() + b"<script>alert(1)</script><?php evil();?>"
        _, stored = self.saved(raw)
        self.assertNotIn(b"<script>", stored)
        self.assertNotIn(b"evil", stored)

    def test_exactly_8mb_accepted_8mb_plus_one_rejected(self):
        base = jpeg_bytes(size=(100, 100))
        exact = base + b"\0" * (photo.MAX_BYTES - len(base))
        self.assertEqual(len(exact), photo.MAX_BYTES)
        self.saved(exact)
        res = self.request(title="큼", photo=up(exact + b"\0"))
        self.assertContains(res, "8MB", status_code=400)

    def test_png_modes_do_not_crash(self):
        for mode in ("1", "L", "LA", "P", "RGB", "RGBA"):
            buf = io.BytesIO()
            Image.new(mode, (30, 30)).save(buf, "PNG")
            with Image.open(io.BytesIO(photo.reencode(io.BytesIO(buf.getvalue())))) as im:
                self.assertEqual((im.format, im.mode), ("JPEG", "RGB"), mode)

    def test_16bit_gray_png_rejected_or_kept_never_500(self):
        buf = io.BytesIO()
        Image.new("I;16", (30, 30)).save(buf, "PNG")
        self.assertIn(self.request(photo=up(buf.getvalue(), "a.png")).status_code, (302, 400))

    def test_broken_exif_does_not_500(self):
        """EXIF 세그먼트가 망가져도 500이 아니다(저장되거나 안내)."""
        good = jpeg_bytes(size=(50, 50))
        seg = b"Exif\x00\x00MM\x00\x2a\xff\xff\xff\xff" + b"\x00" * 20
        bad = good[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + good[2:]
        self.assertIn(self.request(photo=up(bad)).status_code, (302, 400))

    def test_bad_orientation_value_does_not_500(self):
        out = io.BytesIO()
        ex = Image.Exif()
        ex[0x0112] = 99
        Image.new("RGB", (40, 20)).save(out, "JPEG", exif=ex)
        self.assertEqual(self.request(photo=up(out.getvalue())).status_code, 302)

    def test_jpeg_bomb_header_rejected_before_decoding(self):
        """머리만 8000×8000인 JPEG(6400만 화소). SOF의 크기로 거부하고 픽셀을 풀지 않는다."""
        raw = bytearray(jpeg_bytes(size=(16, 16)))
        i = raw.index(b"\xff\xc0")
        raw[i + 5:i + 9] = (8000).to_bytes(2, "big") + (8000).to_bytes(2, "big")
        with mock.patch.object(Image.Image, "load", side_effect=AssertionError("풀면 안 된다")) as _:
            res = self.request(photo=up(bytes(raw)))
        self.assertContains(res, "화소", status_code=400)

    def test_at_pixel_limit_accepted(self):
        """2000만 화소 폰 사진 크기(5184×3888)의 PNG는 받는다(거부 경계)."""
        buf = io.BytesIO()
        Image.new("L", (5184, 3888)).save(buf, "PNG", compress_level=9)
        self.assertLess(len(buf.getvalue()), photo.MAX_BYTES)
        t = time.monotonic()
        data = photo.reencode(io.BytesIO(buf.getvalue()))
        self.assertLess(time.monotonic() - t, 15)
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(max(im.size), 1600)

    def test_file_mode_private(self):
        r, _ = self.saved(jpeg_bytes())
        self.assertEqual(os.stat(r.photo.path).st_mode & 0o777, 0o600)

    def test_media_url_does_not_serve_photo(self):
        r, _ = self.saved(jpeg_bytes())
        for prefix in ("/media/", "/static/", "/"):
            self.assertEqual(self.manager.get(prefix + r.photo.name).status_code, 404, prefix)


class ForgeryTests(QaBase):
    def test_csrf_enforced_on_every_post(self):
        r = self.make()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u4)
        for url in (f"{self.base}/new", f"{self.base}/{r.key}/state", f"{self.base}/{r.key}/edit",
                    f"{self.base}/{r.key}/delete", f"/s/{self.hub.token}/school/members/{self.m5.key}/repair"):
            self.assertEqual(c.post(url, {"status": "done", "on": "1", "cat": "info", "title": "x"}).status_code, 403, url)
        r.refresh_from_db()
        self.assertEqual(r.status, Repair.Status.RECEIVED)

    def test_get_cannot_change(self):
        r = self.make()
        for path in (f"/{r.key}/state", f"/{r.key}/delete"):
            self.assertEqual(self.manager.get(f"{self.base}{path}?status=done").status_code, 405, path)
        url = f"/s/{self.hub.token}/school/members/{self.m5.key}/repair?on=1"
        self.assertEqual(self.manager.get(url).status_code, 405)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.can_repair)

    def test_state_forged_with_htmx_header_and_group_office(self):
        r = self.make()
        Member.objects.filter(pk=self.m4b.pk).update(group="admin", position="행정실장", cls="행정실")
        res = self.teacher.post(f"{self.base}/{r.key}/state", {"status": "done", "reply": "위조"}, headers={"HX-Request": "true"})
        self.assertEqual(res.status_code, 403)
        r.refresh_from_db()
        self.assertEqual((r.status, r.reply), (Repair.Status.RECEIVED, ""))
        self.assertNotContains(self.teacher.get(f"{self.base}/{r.key}"), "<select")

    def test_mass_assignment_on_new_and_edit(self):
        extra = {"status": "done", "reply": "위조", "by": str(self.m4.pk), "by_name": "교장", "space": "1", "key": "aaa", "stored": "0"}
        self.assertEqual(self.request(**extra).status_code, 302)
        r = Repair.objects.in_space(self.hub).get()
        self.assertEqual((r.status, r.reply, r.by_id, r.by_name), (Repair.Status.RECEIVED, "", self.m4b.pk, "정하은"))
        key = r.key
        self.teacher.post(f"{self.base}/{key}/edit", {"cat": "info", "title": "고침", **extra})
        r.refresh_from_db()
        self.assertEqual((r.key, r.status, r.reply, r.by_id, r.by_name), (key, Repair.Status.RECEIVED, "", self.m4b.pk, "정하은"))

    def test_self_promote_through_profile_form(self):
        self.other.post(f"/s/{self.hub.token}/school/me", {"group": "admin", "role": "행정실장", "can_repair": "1", "is_manager": "1"})
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.can_repair)
        self.assertFalse(self.m5.is_manager)
        # 행정실로 옮겨도 🔔·담당 칸·처리 카드 없음(담당자 없으면 관리자만 받는다)
        self.request(title="그룹 시험")
        r = Repair.objects.in_space(self.hub).get()
        self.assertFalse(Notification.objects.in_space(self.hub).filter(member=self.m5).filter(kind=KIND.REPAIR).exists())
        self.assertTrue(Notification.objects.in_space(self.hub).filter(member=self.m4, kind=KIND.REPAIR).exists())
        page = self.other.get(self.base)
        self.assertNotContains(page, "행정실 · 김도윤")
        self.assertNotContains(self.other.get(f"{self.base}/{r.key}"), "<select")

    def test_handler_is_revoked_immediately(self):
        r = self.make()
        self.set_handler(self.m5)
        self.assertEqual(self.other.post(f"{self.base}/{r.key}/state", {"status": "visit"}).status_code, 303)
        self.manager.post(f"/s/{self.hub.token}/school/members/{self.m5.key}/repair", {"on": "0"})
        self.assertEqual(self.other.post(f"{self.base}/{r.key}/state", {"status": "done"}).status_code, 403)

    def test_handler_cannot_appoint_handler(self):
        self.set_handler(self.m5)
        res = self.other.post(f"/s/{self.hub.token}/school/members/{self.m4b.key}/repair", {"on": "1"})
        self.assertEqual(res.status_code, 403)
        self.m4b.refresh_from_db()
        self.assertFalse(self.m4b.can_repair)

    def test_requester_blocked_after_start_for_every_status_and_returning_received_reopens(self):
        r = self.make()
        for st in (Repair.Status.VISIT, Repair.Status.DOING, Repair.Status.DONE):
            Repair.objects.filter(pk=r.pk).update(status=st)
            self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "x"}).status_code, 403, st)
            self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/delete").status_code, 403, st)
            self.assertNotContains(self.teacher.get(f"{self.base}/{r.key}"), f"{r.key}/edit")
        r.refresh_from_db()
        self.assertEqual(r.title, "형광등")

    def test_edit_rejected_inside_lock_if_started_between_read_and_save(self):
        """화면을 연 뒤 담당자가 시작했으면 저장이 거부되고 값도 사진도 그대로."""
        r = self.make()
        real = views._can_edit
        calls = []

        def racing(member, rep):
            calls.append(1)
            ok = real(member, rep)
            if len(calls) == 1:   # 첫 검사 직후 담당자가 시작
                Repair.objects.filter(pk=rep.pk).update(status=Repair.Status.DOING)
            return ok
        with mock.patch.object(views, "_can_edit", racing):
            res = self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "경쟁", "photo": up(jpeg_bytes())})
        self.assertEqual(res.status_code, 403)
        r.refresh_from_db()
        self.assertEqual((r.title, bool(r.photo)), ("형광등", False))

    def test_left_member_loses_everything_request_survives(self):
        self.request(title="나간 사람 요청", photo=up(jpeg_bytes()))
        r = Repair.objects.in_space(self.hub).get()
        Member.objects.filter(pk=self.m4b.pk).delete()
        r.refresh_from_db()
        self.assertIsNone(r.by_id)
        self.assertEqual(r.by_name, "정하은")
        for path in ("", f"/{r.key}", f"/{r.key}/photo"):
            self.assertEqual(self.teacher.get(f"{self.base}{path}").status_code, 404, path)
        self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/delete").status_code, 404)
        page = self.manager.get(f"{self.base}/{r.key}")
        self.assertContains(page, "정하은")
        self.assertNotContains(page, f"{r.key}/edit")

    def test_old_grade_token_never_serves_repairs_to_strangers(self):
        r = self.make()
        far = self.as_(self.far_user)
        self.assertEqual(far.get(f"/s/{self.g4.token}/repairs/{r.key}").status_code, 404)
        self.assertEqual(far.get(f"/s/{self.g4.token}/repairs/{r.key}/photo").status_code, 404)
        res = self.teacher.get(f"/s/{self.g4.token}/repairs/{r.key}")
        self.assertEqual(res.status_code, 302)
        self.assertIn(f"/s/{self.hub.token}/repairs/{r.key}", res["Location"])

    def test_grade_space_member_of_other_school_and_token_swap(self):
        r = self.make()
        u = get_user_model().objects.create(username="google:lone", first_name="외부")
        grade = Space.objects.create(school=self.far_school, grade=3, name="먼초 3학년")
        Member.objects.create(space=grade, user=u, name="외부", cls="3-1")
        c = self.as_(u)
        for url in (self.base, f"{self.base}/{r.key}", f"{self.base}/{r.key}/photo", f"/s/{grade.token}/repairs/{r.key}", f"/s/{grade.token}/repairs/{r.key}/photo"):
            self.assertEqual(c.get(url).status_code, 404, url)
        for url in (f"{self.base}/{r.key}/state", f"/s/{grade.token}/repairs/{r.key}/state",
                    f"/s/{self.hub.token}/school/members/{self.m5.key}/repair", f"/s/{grade.token}/school/members/{self.m5.key}/repair"):
            self.assertEqual(c.post(url, {"status": "done", "on": "1"}).status_code, 404, url)

    def test_manager_cannot_appoint_member_of_other_space(self):
        res = self.manager.post(f"/s/{self.hub.token}/school/members/{self.far_member.key}/repair", {"on": "1"})
        self.assertEqual(res.status_code, 404)
        self.far_member.refresh_from_db()
        self.assertFalse(self.far_member.can_repair)


class FlowEdgeTests(QaBase):
    def test_tab_count_is_open_only_on_board_and_repairs(self):
        self.make(title="a")
        self.make(title="b", status=Repair.Status.VISIT)
        self.make(title="c", status=Repair.Status.DONE)
        for url in (self.base, f"/s/{self.hub.token}/board"):
            self.assertRegex(self.teacher.get(url).content.decode(), r'수리 요청 <span class="c num">2</span>', url)
        Repair.objects.in_space(self.hub).update(status=Repair.Status.DONE)
        self.assertNotRegex(self.teacher.get(self.base).content.decode(), r'수리 요청 <span class="c num">')

    def test_notice_tab_count_same_on_both_tabs_with_hidden_notice(self):
        Notice.objects.create(space=self.hub, by=self.m4, by_name="송민재", title="모두")
        board = self.teacher.get(f"/s/{self.hub.token}/board").content.decode()
        rep = self.teacher.get(self.base).content.decode()
        self.assertIn('공지 <span class="c num">1</span>', board)
        self.assertIn('공지 <span class="c num">1</span>', rep)

    def test_done_goes_after_all_open_across_pages(self):
        done = [self.make(title=f"끝 {i}", status=Repair.Status.DONE) for i in range(3)]
        opens = [self.make(title=f"열림 {i}") for i in range(20)]
        html = self.teacher.get(self.base).content.decode()
        self.assertNotIn("끝 0", html)
        self.assertIn(opens[0].title, html)
        self.assertIn("끝 2", self.teacher.get(f"{self.base}?page=2").content.decode())
        self.assertEqual(len(done), 3)

    def test_bell_click_opens_detail_and_marks_read(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.request()
        r = Repair.objects.in_space(self.hub).get()
        n = Notification.objects.in_space(self.hub).get(member=self.m5, kind=KIND.REPAIR)
        res = self.other.post(f"/s/{self.hub.token}/bell/{n.key}")
        self.assertEqual(res["Location"], f"{self.base}/{r.key}")
        n.refresh_from_db()
        self.assertTrue(n.read)
        # 남의 🔔은 못 누른다
        self.assertEqual(self.teacher.post(f"/s/{self.hub.token}/bell/{n.key}").status_code, 404)

    def test_requester_bell_for_each_status_change(self):
        self.request()
        r = Repair.objects.in_space(self.hub).get()
        for st, label in (("visit", "방문 예정"), ("doing", "처리 중"), ("done", "완료")):
            self.manager.post(f"{self.base}/{r.key}/state", {"status": st, "reply": ""})
            n = Notification.objects.in_space(self.hub).get(member=self.m4b, kind=KIND.REPAIR_UP, read=False)
            self.assertIn(label, n.text)
            self.assertEqual(n.link, f"{self.base}/{r.key}")
            self.assertContains(self.teacher.get(f"/s/{self.hub.token}/bell"), label)
            Notification.objects.filter(pk=n.pk).update(read=True)

    def test_recategorize_rings_the_new_desk(self):
        """요청자가 분류를 고치면 새 분류 담당자가 알아야 한다(아무도 모르는 요청이 남지 않게)."""
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True, group="admin")   # 시설
        Member.objects.filter(pk=self.m4.pk).update(can_repair=True)   # 정보
        self.request(cat="facility", title="잘못 고른 분류")
        r = Repair.objects.in_space(self.hub).get()
        Notification.objects.in_space(self.hub).filter(member=self.m4).delete()
        self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "잘못 고른 분류"}).status_code, 302)
        self.assertTrue(Notification.objects.in_space(self.hub).filter(member=self.m4, repair=r, kind=KIND.REPAIR).exists())

    def test_digest_sends_repair_title_and_marks_pushed(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.m5.refresh_from_db()
        PushSubscription.objects.create(space=self.hub, member=self.m5, endpoint="https://fcm.googleapis.com/x", p256dh="a", auth="b")
        Notification.objects.in_space(self.hub).filter(member=self.m5).delete()   # 픽스처 알림이 첫 제목이 되지 않게
        self.request(title="전자칠판 소리")
        now = datetime(2026, 11, 4, 8, 25, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertIsNotNone(push.current_slot(timezone.localtime(now)))
        with override_settings(VAPID_PUBLIC_KEY="x", VAPID_PRIVATE_KEY="y"), mock.patch.object(push, "_send") as send, \
                mock.patch("collect.push.neis.day_off", return_value=False):
            self.assertGreaterEqual(push.digest(now), 1)
        mine = [c.args[1] for c in send.call_args_list if c.args[0].member_id == self.m5.pk]
        self.assertTrue(mine)
        self.assertIn("전자칠판 소리", mine[0])
        self.assertTrue(Notification.objects.in_space(self.hub).get(member=self.m5, kind=KIND.REPAIR).pushed)

    def test_no_vendor_wording_when_no_vendor(self):
        r = self.make(cat="info")
        for url in (self.base, f"{self.base}/new", f"{self.base}/{r.key}", f"{self.base}/{r.key}/edit"):
            html = self.teacher.get(url).content.decode()
            for word in ("업체", "티로드", "문자", "휴대폰"):
                self.assertNotIn(word, html.split("</head>", 1)[-1], f"{url} {word}")


class XssAndLengthTests(QaBase):
    X = '"><script>alert(1)</script><img src=x onerror=alert(2)>'

    def assertClean(self, res, status=200):
        self.assertEqual(res.status_code, status)
        html = res.content.decode()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)

    def test_everywhere(self):
        Member.objects.filter(pk=self.m4b.pk).update(name=self.X[:40])
        Member.objects.filter(pk=self.m5.pk).update(name=self.X[:40], can_repair=True)
        self.assertEqual(self.request(title=self.X, note=self.X, place=self.X).status_code, 302)
        r = Repair.objects.in_space(self.hub).get()
        res = self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": self.X}, headers={"HX-Request": "true"})
        self.assertClean(res)
        Repair.objects.filter(pk=r.pk).update(status="received")
        for c, url in ((self.teacher, self.base), (self.teacher, f"{self.base}/{r.key}"), (self.teacher, f"{self.base}/{r.key}/edit"),
                       (self.teacher, f"{self.base}/new"), (self.manager, f"/s/{self.hub.token}/bell"), (self.teacher, f"/s/{self.hub.token}/bell"),
                       (self.manager, f"/s/{self.hub.token}/school")):
            self.assertClean(c.get(url))
        self.assertClean(self.request(title=self.X, note="가" * 1001), status=400)

    def test_max_lengths_save_and_bell_fits_columns(self):
        self.assertEqual(self.request(title="가" * 200, place="나" * 100, note="다" * 1000).status_code, 302)
        r = Repair.objects.in_space(self.hub).get()
        n = Notification.objects.in_space(self.hub).get(kind=KIND.REPAIR)
        self.assertLessEqual(len(n.text), 300)
        self.assertLessEqual(len(n.link), 300)
        res = self.manager.post(f"{self.base}/{r.key}/state", {"status": "done", "reply": "라" * 500})
        self.assertEqual(res.status_code, 303)
        self.assertLessEqual(len(Notification.objects.in_space(self.hub).get(kind=KIND.REPAIR_UP).text), 300)
        self.assertEqual(self.teacher.get(self.base).status_code, 200)

    def test_emoji_title_at_limit(self):
        self.assertEqual(self.request(title="🔧" * 200).status_code, 302)


class CountAndQuotaTests(QaBase):
    def test_absolute_queries_and_flat_with_50_rows_all_pages(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        for i in range(3):
            self.request(title=f"수리 {i}")
        with CaptureQueriesContext(connection) as few_detail:
            self.manager.get(f"{self.base}/{Repair.objects.in_space(self.hub).first().key}")
        few_detail = len(few_detail)   # 다음 요청이 쿼리 로그를 비우므로 바로 센다
        with CaptureQueriesContext(connection) as few_bell:
            self.other.get(f"/s/{self.hub.token}/bell")
        few_bell = len(few_bell)
        self.teacher.get(self.base)  # 제출 뒤 토스트를 세션에서 비우는 한 번(이 쓰기는 세지 않는다)
        with CaptureQueriesContext(connection) as few:
            self.teacher.get(self.base)
        few = len(few)
        for i in range(47):
            self.request(title=f"더 {i}", place="3층", note="메모")
        self.teacher.get(self.base)
        Repair.objects.in_space(self.hub).update(reply="답")
        for url_page in (1, 2, 3):
            with CaptureQueriesContext(connection) as many:
                self.assertEqual(self.teacher.get(f"{self.base}?page={url_page}").status_code, 200)
            self.assertEqual(len(many), few, url_page)
        self.assertLess(few, 20)
        with CaptureQueriesContext(connection) as many_detail:
            self.manager.get(f"{self.base}/{Repair.objects.in_space(self.hub).first().key}")
        self.assertEqual(len(many_detail), few_detail)
        with CaptureQueriesContext(connection) as many_bell:
            self.other.get(f"/s/{self.hub.token}/bell")
        self.assertEqual(len(many_bell), few_bell)

    def test_repair_photos_use_up_quota_for_notice_and_hwp(self):
        self.request(photo=up(jpeg_bytes()))
        r = Repair.objects.in_space(self.hub).get()
        self.assertGreater(r.stored, 0)
        self.assertEqual(r.stored, default_storage.size(r.photo.name))
        before = SourceFile.used_bytes(self.hub)
        self.assertGreaterEqual(before, r.stored)
        pdf = b"%PDF-1.7\n" + b"x" * 2000
        with override_settings(HWP_SCHOOL_QUOTA=before + 100):
            res = self.manager.post(f"/s/{self.hub.token}/board/new", {"title": "첨부", "body": "b", "attach": up(pdf, "a.pdf")})
        self.assertContains(res, "저장 공간", status_code=400)

    def test_rejected_upload_leaves_no_file(self):
        before = _media_files()
        self.request(photo=up(b"junk"))
        self.assertEqual(_media_files() - before, set())

    def test_quota_rejected_photo_leaves_no_file(self):
        before = _media_files()
        with override_settings(HWP_SCHOOL_QUOTA=0):
            self.assertEqual(self.request(photo=up(jpeg_bytes())).status_code, 400)
        self.assertEqual(_media_files() - before, set())


def _media_files():
    from django.conf import settings
    return {os.path.join(d, f) for d, _, fs in os.walk(settings.MEDIA_ROOT) for f in fs}


class PurgeCommandTests(QaBase):
    def row(self, days, status, title, with_photo=True):
        self.request(title=title, **({"photo": up(jpeg_bytes())} if with_photo else {}))
        r = Repair.objects.in_space(self.hub).get(title=title)
        Repair.objects.filter(pk=r.pk).update(status=status, updated_at=timezone.now() - timedelta(days=days))
        r.refresh_from_db()
        return r

    def run_purge(self, **kw):
        out = io.StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("purge", stdout=out, **kw)
        return out.getvalue()

    def test_command_deletes_rows_files_bells_and_prints_counts_only(self):
        D, W = Repair.Status.DONE, Repair.Status.DOING
        gone = [self.row(91, D, "완료91-비밀"), self.row(366, W, "진행366-비밀"), self.row(366, Repair.Status.RECEIVED, "접수366-비밀")]
        kept = [self.row(89, D, "완료89"), self.row(364, W, "진행364"), self.row(364, Repair.Status.VISIT, "방문364")]
        names = [r.photo.name for r in gone]
        out = self.run_purge()
        self.assertIn("지난 수리 요청 3건", out)
        self.assertNotIn("비밀", out)
        self.assertEqual({r.title for r in Repair.objects.in_space(self.hub)}, {r.title for r in kept})
        for n in names:
            self.assertFalse(default_storage.exists(n), n)
        for r in kept:
            self.assertTrue(default_storage.exists(r.photo.name), r.title)
        self.assertEqual(sum(r.stored for r in Repair.objects.in_space(self.hub)), sum(r.stored for r in kept))
        self.assertFalse(Notification.objects.in_space(self.hub).filter(repair_id__in=[r.pk for r in gone]).exists())

    def test_dry_run_command_touches_nothing(self):
        r = self.row(91, Repair.Status.DONE, "완료")
        out = self.run_purge(dry_run=True)
        self.assertIn("지난 수리 요청 1건", out)
        self.assertTrue(Repair.objects.filter(pk=r.pk).exists())
        self.assertTrue(default_storage.exists(r.photo.name))

    def test_other_school_row_expires_on_its_own_clock(self):
        from spaces.merge import open_school
        from spaces.test_merge import UNTIL
        far_hub = open_school(self.far_school, UNTIL, self.far_member)
        far = Repair.objects.create(space=far_hub, by_name="다른학교", cat="info", title="남의 것", status="done")
        self.row(91, Repair.Status.DONE, "우리 것")
        self.run_purge()
        self.assertTrue(Repair.objects.filter(pk=far.pk).exists())

    def test_stored_corrected_when_file_missing_and_orphan_collected(self):
        r = self.row(1, Repair.Status.RECEIVED, "파일 사라짐")
        default_storage.delete(r.photo.name)
        stray = default_storage.save(f"repair/{self.hub.pk}/stray.jpg", io.BytesIO(b"x"))
        old = time.time() - 7200
        os.utime(default_storage.path(stray), (old, old))
        self.run_purge()
        r.refresh_from_db()
        self.assertEqual(r.stored, 0)
        self.assertFalse(default_storage.exists(stray))
        self.assertEqual(self.teacher.get(f"{self.base}/{r.key}/photo").status_code, 404)

    def test_space_delete_removes_photo_files(self):
        r = self.row(1, Repair.Status.RECEIVED, "공간 삭제")
        with self.captureOnCommitCallbacks(execute=True):
            Space.objects.filter(pk=self.hub.pk).delete()
        self.assertFalse(default_storage.exists(r.photo.name))


class ProfileSanityTests(QaBase):
    def test_exif_transpose_helper_is_what_we_think(self):
        """위 표본의 역변환 표가 Pillow와 같은지(검증 자체의 전제)."""
        for o, inv in INVERSE.items():
            ex = Image.Exif()
            ex[0x0112] = o
            buf = io.BytesIO()
            quadrants((40, 20)).transpose(inv).save(buf, "PNG", exif=ex)
            with Image.open(io.BytesIO(buf.getvalue())) as im:
                self.assertEqual(ImageOps.exif_transpose(im).size, (40, 20))


class RealRateLimitTests(QaBase):
    def test_31st_upload_in_hour_is_429_and_keeps_form_other_member_unaffected(self):
        for i in range(views.UPLOAD_PER_HOUR):
            self.assertEqual(self.request(title=f"사진 {i}", photo=up(jpeg_bytes())).status_code, 302, i)
        res = self.request(title="서른한 번째", photo=up(jpeg_bytes()))
        self.assertContains(res, 'value="서른한 번째"', status_code=429)
        self.assertFalse(Repair.objects.in_space(self.hub).filter(title="서른한 번째").exists())
        self.assertEqual(self.request(title="사진 없이는 됨").status_code, 302)
        self.assertEqual(self.request(self.other, title="다른 회원", photo=up(jpeg_bytes())).status_code, 302)
