"""K1 수리 요청: 흐름·권한·사진·격리·보관. 담당은 Member.can_repair(행정실 그룹이 아니다)."""
import io
import struct
import zlib
from datetime import timedelta
from unittest import mock

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image

from accounts.models import new_guest
from collect.models import Notification
from collect.push import _title
from hwpfiles.models import SourceFile
from hwpfiles.retention import orphan_files, purge
from spaces.merge import open_school
from spaces.models import Member, MemberLog, School
from spaces.test_merge import UNTIL, MergeBase

from . import photo, views
from .models import Repair, VendorAccount

KIND = Notification.Kind


def jpeg_bytes(size=(64, 48), color=(200, 40, 40), **save):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG", **save)
    return out.getvalue()


def png_bytes(size=(64, 48), mode="RGB", color=(40, 200, 40)):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, "PNG")
    return out.getvalue()


def gps_exif(orientation=None):
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    if orientation:
        exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (37.0, 30.0, 0.0)
    return exif


def png_header_only(width, height):
    """머리만 거대한 PNG(픽셀 폭탄): 크기를 읽는 단계에서 거부돼야 한다."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    head = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(b"\0" * 100)) + chunk(b"IEND", b"")


def up(data, name="p.jpg"):
    return SimpleUploadedFile(name, data, content_type="image/jpeg")


class Base(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)  # m4 관리자(g4), m4b(g4), m5(g5)
        for m in (self.m4, self.m4b, self.m5):
            m.refresh_from_db()
        self.base = f"/s/{self.hub.token}/repairs"
        self.manager, self.teacher, self.other = self.as_(self.u4), self.as_(self.u4b), self.as_(self.u5)

    def as_(self, user):
        c = Client()
        c.force_login(user)
        return c

    def request(self, client=None, **data):
        form = {"cat": "facility", "place": "4-2 교실", "title": "뒷문이 안 닫혀요", "note": "", **data}
        return (client or self.teacher).post(f"{self.base}/new", form)

    def make(self, by=None, **kw):
        by = by or self.m4b
        return Repair.objects.create(space=self.hub, by=by, by_name=by.name, **{"cat": "facility", "title": "형광등", "place": "3층", **kw})

    def set_handler(self, member):
        return self.manager.post(f"/s/{self.hub.token}/school/members/{member.key}/repair", {"on": "1"})


class FlowTests(Base):
    def test_request_to_done_rings_handler_then_requester_once(self):
        self.assertEqual(self.set_handler(self.m5).status_code, 302)
        self.assertEqual(self.request(urgent="on", note="2블록 말고").status_code, 302)
        r = Repair.objects.in_space(self.hub).get()
        self.assertEqual((r.status, r.by_id, r.by_name, r.urgent, r.note), (Repair.Status.RECEIVED, self.m4b.pk, "정하은", True, "2블록 말고"))
        bell = Notification.objects.in_space(self.hub).get(member=self.m5, kind=KIND.REPAIR)
        self.assertEqual((bell.repair_id, bell.link, bell.pushed), (r.pk, f"{self.base}/{r.key}", False))
        self.assertEqual(self.other.post(f"/s/{self.hub.token}/bell/{bell.key}").status_code, 302)

        url = f"{self.base}/{r.key}/state"
        self.assertEqual(self.other.post(url, {"status": "visit", "reply": "내일 오전 갑니다"}).status_code, 303)
        mine = Notification.objects.in_space(self.hub).filter(member=self.m4b, kind=KIND.REPAIR_UP)
        self.assertEqual(mine.count(), 1)
        self.assertIn("방문 예정", mine.get().text)
        # 안 읽은 사이 또 바뀌면 쌓지 않고 한 건을 고쳐 묶음에 다시 넣는다
        Notification.objects.filter(pk=mine.get().pk).update(pushed=True)
        self.other.post(url, {"status": "done", "reply": "교체했어요"})
        self.assertEqual(mine.count(), 1)
        self.assertIn("완료", mine.get().text)
        self.assertFalse(mine.get().pushed)
        r.refresh_from_db()
        self.assertEqual((r.status, r.reply), (Repair.Status.DONE, "교체했어요"))
        # 읽은 뒤 또 바뀌면 새 한 건
        Notification.objects.filter(pk=mine.get().pk).update(read=True)
        self.other.post(url, {"status": "doing", "reply": "교체했어요"})
        self.assertEqual(mine.count(), 2)

    def test_reply_only_does_not_ring(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        r = self.make()
        self.other.post(f"{self.base}/{r.key}/state", {"status": "received", "reply": "확인했어요"})
        self.assertFalse(Notification.objects.in_space(self.hub).filter(member=self.m4b, kind__in=[KIND.REPAIR, KIND.REPAIR_UP]).exists())

    def test_own_status_change_does_not_ring_self(self):
        r = self.make(by=self.m4)
        self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": ""})
        self.assertFalse(Notification.objects.in_space(self.hub).filter(member=self.m4, kind=KIND.REPAIR_UP).exists())

    def test_requester_who_left_is_skipped(self):
        r = self.make()
        Member.objects.filter(pk=self.m4b.pk).delete()
        self.assertEqual(self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": ""}).status_code, 303)

    def test_handler_bell_clears_once_started(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.request()
        r = Repair.objects.in_space(self.hub).get()
        self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": ""})
        self.assertTrue(Notification.objects.in_space(self.hub).get(member=self.m5, kind=KIND.REPAIR).read)

    def test_no_handler_rings_managers(self):
        self.request()
        self.assertTrue(Notification.objects.in_space(self.hub).filter(member=self.m4, kind=KIND.REPAIR).exists())

    def test_requester_handler_not_rung_for_own_request(self):
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=True)
        self.request()
        self.assertFalse(Notification.objects.in_space(self.hub).filter(member=self.m4b, kind__in=[KIND.REPAIR, KIND.REPAIR_UP]).exists())

    def test_bell_routes_by_desk(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True, group="admin")
        Member.objects.filter(pk=self.m4.pk).update(can_repair=True)
        self.request(cat="facility", title="시설")
        self.request(cat="info", title="정보")
        bells = Notification.objects.in_space(self.hub).filter(kind=KIND.REPAIR)
        self.assertEqual(bells.get(member=self.m5).repair.title, "시설")
        self.assertEqual(bells.get(member=self.m4).repair.title, "정보")

    def test_push_title_uses_repair_title(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.request(title="전자칠판 소리")
        self.assertEqual(_title(Notification.objects.in_space(self.hub).get(member=self.m5, kind=KIND.REPAIR)), "전자칠판 소리")

    def test_bad_input(self):
        self.assertEqual(self.request(title="  ").status_code, 400)
        self.assertEqual(self.request(cat="vendor").status_code, 400)
        self.assertEqual(self.request(title="가" * 201).status_code, 400)
        self.assertEqual(self.request(place="가" * 101).status_code, 400)
        self.assertEqual(self.request(note="가" * 1001).status_code, 400)
        self.assertFalse(Repair.objects.in_space(self.hub).exists())
        r = self.make()
        self.assertEqual(self.manager.post(f"{self.base}/{r.key}/state", {"status": "nope"}).status_code, 400)
        self.assertEqual(self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": "가" * 501}).status_code, 400)

    def test_nul_is_stripped(self):
        self.assertEqual(self.request(title="문\x00고장").status_code, 302)
        self.assertEqual(Repair.objects.in_space(self.hub).get().title, "문고장")

    def test_default_place(self):
        Member.objects.filter(pk=self.m4b.pk).update(cls="4-2")
        self.assertContains(self.teacher.get(f"{self.base}/new"), 'value="4-2 교실"')
        Member.objects.filter(pk=self.m4b.pk).update(cls="4-전담")
        self.assertNotContains(self.teacher.get(f"{self.base}/new"), "전담 교실")
        Member.objects.filter(pk=self.m4b.pk).update(cls="행정실")
        self.assertContains(self.teacher.get(f"{self.base}/new"), 'value="행정실"')
        Member.objects.filter(pk=self.m4b.pk).update(cls="기타/지원", group="support")
        self.assertNotContains(self.teacher.get(f"{self.base}/new"), 'value="기타/지원"')

    def test_warning_text(self):
        page = self.teacher.get(f"{self.base}/new")
        self.assertContains(page, "정보 담당 선생님이 받습니다")
        self.assertContains(page, "학생 이름·개인정보는 적지 마세요")
        self.assertNotContains(page, "외부 업체가 볼 수 있어요")
        VendorAccount.issue(self.hub, "한빛정보", "강업체", UNTIL, self.m4)
        page = self.teacher.get(f"{self.base}/new")
        self.assertContains(page, "이 요청은 외부 업체(한빛정보)가 볼 수 있어요. 이름 대신 위치만 보이고, 학생 이름·개인정보는 적지 마세요")


class ScanBombTests(TestCase):
    """점진 JPEG 폭탄: 픽셀을 풀기 전에 스캔 수로 거부. 보통 점진 JPEG는 통과."""

    def test_normal_progressive_passes(self):
        raw = jpeg_bytes((800, 600), progressive=True)
        self.assertLess(raw.count(b"\xff\xda"), photo.MAX_SCANS)
        self.assertTrue(photo.reencode(io.BytesIO(raw)))

    def test_too_many_scans_refused_before_decoding(self):
        raw = jpeg_bytes()[:-2] + b"\xff\xda\x00\x02" * (photo.MAX_SCANS + 1) + b"\xff\xd9"
        with mock.patch.object(photo.Image, "open") as opened, self.assertRaises(photo.BadPhoto) as e:
            photo.reencode(io.BytesIO(raw))
        opened.assert_not_called()
        self.assertEqual(str(e.exception), photo.TOO_MANY_SCANS)

    def test_png_with_same_bytes_not_counted(self):
        raw = png_bytes()
        self.assertTrue(photo.reencode(io.BytesIO(raw + b"\xff\xda" * (photo.MAX_SCANS + 1))))


class ListTests(Base):
    def test_counts_order_and_columns(self):
        self.make(title="끝난 것", status=Repair.Status.DONE, reply="패킹 교체")
        new = self.make(title="급한 것", cat="info", urgent=True)
        page = self.teacher.get(self.base)
        self.assertContains(page, "진행 중 <b class=\"num\">1</b>건 · 전체 <b class=\"num\">2</b>건", html=False)
        html = page.content.decode()
        self.assertLess(html.index("급한 것"), html.index("끝난 것"))
        self.assertContains(page, "↳ 패킹 교체")
        self.assertContains(page, "급함")
        self.assertContains(page, new.place)
        self.assertContains(page, new.by_name)

    def test_done_urgent_loses_badge(self):
        self.make(urgent=True, status=Repair.Status.DONE)
        self.assertNotContains(self.teacher.get(self.base), ">급함<")

    def test_everyone_sees_both_categories(self):
        self.make(title="시설건")
        self.make(title="정보건", cat="info")
        page = self.other.get(self.base)
        self.assertContains(page, "시설건")
        self.assertContains(page, "정보건")

    def test_who_receives_card_is_designated_not_group(self):
        Member.objects.filter(pk=self.m5.pk).update(group="admin")  # 그룹만 옮긴 사람
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=True, group="admin")
        page = self.teacher.get(self.base)
        self.assertContains(page, "행정실 · 정하은")
        self.assertNotContains(page, "행정실 · 김도윤")

    def test_desk_label_never_claims_a_group_nobody_is_in(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)  # 5학년 담당자 한 명, 행정실 없음
        page = self.teacher.get(self.base)
        self.assertNotContains(page, "행정실 · 김도윤")
        self.assertContains(page, "담당 · 김도윤")
        Member.objects.filter(pk=self.m5.pk).update(group="admin")  # 행정실 한 명, 정보 쪽 없음
        page = self.teacher.get(self.base)
        self.assertContains(page, "행정실 · 김도윤")
        self.assertNotContains(page, "정보 담당 · 김도윤")

    def test_toast_after_submit_once(self):
        res = self.request()
        self.assertEqual(res.status_code, 302)
        page = self.teacher.get(self.base)
        self.assertContains(page, 'data-toast-now="접수했어요. 담당 선생님 🔔에 떴어요."')
        self.assertNotContains(self.teacher.get(self.base), "data-toast-now")

    def test_toast_when_nobody_was_rung(self):
        self.request(client=self.manager)  # 관리자 한 명뿐: 자기에게는 안 울린다
        self.assertContains(self.manager.get(self.base), 'data-toast-now="접수했어요."')

    def test_no_handler_tells_manager_to_designate(self):
        self.assertContains(self.manager.get(self.base), "수리 담당자를 지정해 주세요")
        self.assertNotContains(self.teacher.get(self.base), "수리 담당자를 지정해 주세요")

    def test_board_has_both_tabs(self):
        self.make()
        self.make(status=Repair.Status.DONE)
        page = self.teacher.get(f"/s/{self.hub.token}/board")
        self.assertContains(page, f'href="{self.base}"')
        self.assertContains(self.teacher.get(self.base), f'href="/s/{self.hub.token}/board"')

    def test_query_count_flat(self):
        for i in range(3):
            self.make(title=f"수리 {i}")
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(self.teacher.get(self.base).status_code, 200)
        for i in range(47):
            self.make(title=f"더 {i}", photo="repair/x.jpg", reply="답")
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(self.teacher.get(self.base).status_code, 200)
        self.assertEqual(len(few), len(many))

    def test_pages(self):
        for i in range(21):
            self.make(title=f"수리 {i}")
        self.assertContains(self.teacher.get(self.base), "1 / 2쪽")
        self.assertContains(self.teacher.get(f"{self.base}?page=2"), "수리 0")

    def test_escapes_html(self):
        x = "<script>alert(1)</script>"
        self.request(title=x, note=x, place=x)
        r = Repair.objects.in_space(self.hub).get()
        self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": x})
        for page in (self.teacher.get(self.base), self.teacher.get(f"{self.base}/{r.key}"),
                     self.teacher.get(f"/s/{self.hub.token}/bell")):
            self.assertNotContains(page, x)
            self.assertContains(page, "&lt;script&gt;")


class PermissionTests(Base):
    def state(self, client, r, status="doing"):
        return client.post(f"{self.base}/{r.key}/state", {"status": status, "reply": ""})

    def test_plain_member_cannot_change_state(self):
        r = self.make(by=self.m4b)
        self.assertEqual(self.state(self.teacher, r).status_code, 403)  # 요청자여도
        self.assertEqual(self.state(self.other, r).status_code, 403)
        r.refresh_from_db()
        self.assertEqual(r.status, Repair.Status.RECEIVED)

    def test_designated_and_manager_can(self):
        r = self.make()
        self.assertEqual(self.state(self.manager, r).status_code, 303)
        self.set_handler(self.m5)
        self.assertEqual(self.state(self.other, r, "done").status_code, 303)

    def test_moving_self_to_office_group_gives_nothing(self):
        r = self.make()
        res = self.other.post(f"/s/{self.hub.token}/school/me", {"group": "admin", "role": "주무관"})
        self.assertLess(res.status_code, 400)
        self.m5.refresh_from_db()
        self.assertEqual(self.m5.group, "admin")
        self.assertEqual(self.state(self.other, r).status_code, 403)
        self.assertNotContains(self.other.get(f"{self.base}/{r.key}"), f"{self.base}/{r.key}/state")

    def test_only_manager_designates_and_google_only(self):
        url = f"/s/{self.hub.token}/school/members/{self.m5.key}/repair"
        self.assertEqual(self.teacher.post(url, {"on": "1"}).status_code, 403)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.can_repair)
        self.assertEqual(self.manager.post(url, {"on": "1"}).status_code, 302)
        self.m5.refresh_from_db()
        self.assertTrue(self.m5.can_repair)
        self.assertEqual(MemberLog.objects.in_space(self.hub).filter(name=self.m5.name).last().after, "수리 담당")
        self.assertEqual(self.manager.post(url, {"on": "0"}).status_code, 302)
        self.m5.refresh_from_db()
        self.assertFalse(self.m5.can_repair)
        g = Member.objects.create(space=self.hub, user=new_guest("손님"), name="손님", cls="교무실", group="office")
        res = self.manager.post(f"/s/{self.hub.token}/school/members/{g.key}/repair", {"on": "1"})
        self.assertEqual(res.status_code, 400)
        g.refresh_from_db()
        self.assertFalse(g.can_repair)

    def test_designate_button_shown_to_manager(self):
        page = self.manager.get(f"/s/{self.hub.token}/school")
        self.assertContains(page, f"/school/members/{self.m5.key}/repair")

    def test_owner_edits_only_while_received(self):
        r = self.make()
        url = f"{self.base}/{r.key}/edit"
        self.assertEqual(self.teacher.get(url).status_code, 200)
        res = self.teacher.post(url, {"cat": "info", "place": "교무실", "title": "새 제목", "note": "메모"})
        self.assertEqual(res.status_code, 302)
        r.refresh_from_db()
        self.assertEqual((r.title, r.cat, r.place, r.note), ("새 제목", "info", "교무실", "메모"))
        Repair.objects.filter(pk=r.pk).update(status=Repair.Status.DOING)
        self.assertEqual(self.teacher.get(url).status_code, 403)
        self.assertEqual(self.teacher.post(url, {"cat": "info", "title": "또"}).status_code, 403)

    def test_others_cannot_edit_or_delete(self):
        r = self.make()
        for c in (self.other, self.manager):
            self.assertEqual(c.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "남"}).status_code, 403)
        self.assertEqual(self.other.post(f"{self.base}/{r.key}/delete").status_code, 403)
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.assertEqual(self.other.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "남"}).status_code, 403)
        self.assertEqual(self.other.post(f"{self.base}/{r.key}/delete").status_code, 403)
        self.assertTrue(Repair.objects.filter(pk=r.pk).exists())

    def test_owner_delete_only_while_received_manager_any_time(self):
        r = self.make()
        self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/delete").status_code, 302)
        self.assertFalse(Repair.objects.filter(pk=r.pk).exists())
        r = self.make(status=Repair.Status.DOING)
        self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/delete").status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/{r.key}/delete").status_code, 302)
        self.assertFalse(Repair.objects.filter(pk=r.pk).exists())

    def test_edit_updates_handler_bell_and_delete_removes_it(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.request(title="학생 김철수 책상")
        r = Repair.objects.in_space(self.hub).get()
        self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "facility", "title": "책상"})
        bell = Notification.objects.in_space(self.hub).get(member=self.m5, kind=KIND.REPAIR)
        self.assertNotIn("김철수", bell.text)
        self.teacher.post(f"{self.base}/{r.key}/delete")
        self.assertFalse(Notification.objects.in_space(self.hub).filter(member=self.m5, kind=KIND.REPAIR).exists())

    def test_recategorize_moves_unread_bells(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True, group="admin")
        Member.objects.filter(pk=self.m4.pk).update(can_repair=True)
        self.request(cat="facility", title="잘못 고름")
        r = Repair.objects.in_space(self.hub).get()
        bells = Notification.objects.in_space(self.hub).filter(repair=r, kind=KIND.REPAIR)
        self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "잘못 고름"})
        self.assertEqual([b.member_id for b in bells], [self.m4.pk])
        self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "제목만"})  # 분류 그대로면 다시 안 울린다
        self.assertEqual(bells.get().text, "제목만")

    def test_recategorize_keeps_read_bell_history(self):
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True, group="admin")
        Member.objects.filter(pk=self.m4.pk).update(can_repair=True)
        self.request(cat="facility", title="x")
        r = Repair.objects.in_space(self.hub).get()
        bells = Notification.objects.in_space(self.hub).filter(repair=r, kind=KIND.REPAIR)
        bells.update(read=True)
        self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "info", "title": "x"})
        self.assertEqual(bells.filter(member=self.m5, read=True).count(), 1)
        self.assertEqual(bells.filter(member=self.m4, read=False).count(), 1)

    def test_buttons_follow_permissions(self):
        r = self.make()
        url = f"{self.base}/{r.key}"
        self.assertContains(self.teacher.get(url), f"{url}/edit")
        self.assertNotContains(self.other.get(url), f"{url}/edit")
        self.assertNotContains(self.other.get(url), f"{url}/delete")
        self.assertContains(self.manager.get(url), f"{url}/delete")
        self.assertContains(self.manager.get(url), f"{url}/state")

    def test_state_htmx_returns_fragment(self):
        r = self.make()
        res = self.manager.post(f"{self.base}/{r.key}/state", {"status": "doing", "reply": "가는 중"}, headers={"HX-Request": "true"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="repairMain"')
        self.assertContains(res, "data-toast-now")
        self.assertNotContains(res, "<html")


class ExpiredTests(Base):
    def setUp(self):
        super().setUp()
        self.r = self.make(photo="")
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))

    def test_read_ok_write_forbidden(self):
        self.assertEqual(self.teacher.get(self.base).status_code, 200)
        self.assertEqual(self.teacher.get(f"{self.base}/{self.r.key}").status_code, 200)
        self.assertEqual(self.request().status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/{self.r.key}/state", {"status": "doing"}).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.base}/{self.r.key}/edit", {"cat": "info", "title": "x"}).status_code, 403)
        url = f"/s/{self.hub.token}/school/members/{self.m5.key}/repair"
        self.assertEqual(self.manager.post(url, {"on": "1"}).status_code, 403)

    def test_expired_screen_has_no_write_controls(self):
        self.assertNotContains(self.manager.get(self.base), f"{self.base}/new")
        page = self.manager.get(f"{self.base}/{self.r.key}")
        self.assertNotContains(page, f"{self.r.key}/state")
        self.assertNotContains(page, f"{self.r.key}/edit")
        self.assertContains(page, f"{self.r.key}/delete")  # 지우기는 개인정보 조치라 남는다

    def test_delete_still_allowed_after_expiry(self):
        self.assertEqual(self.other.post(f"{self.base}/{self.r.key}/delete").status_code, 403)  # 권한은 그대로
        self.assertEqual(self.teacher.post(f"{self.base}/{self.r.key}/delete").status_code, 302)
        started = self.make(status=Repair.Status.DOING)
        self.assertEqual(self.teacher.post(f"{self.base}/{started.key}/delete").status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/{started.key}/delete").status_code, 302)
        self.assertFalse(Repair.objects.in_space(self.hub).exists())

    def test_photo_only_delete_after_expiry(self):
        name = default_storage.save(f"repair/{self.hub.pk}/x.jpg", io.BytesIO(b"abc"))
        Repair.objects.filter(pk=self.r.pk).update(photo=name, stored=3, status=Repair.Status.DOING)
        url = f"{self.base}/{self.r.key}/photo/delete"
        self.assertEqual(self.teacher.post(url).status_code, 403)  # 처리 시작 뒤 요청자는 못 지운다
        self.assertEqual(self.other.post(url).status_code, 403)
        self.assertContains(self.manager.get(f"{self.base}/{self.r.key}"), url)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.manager.post(url).status_code, 303)
        self.r.refresh_from_db()
        self.assertEqual((bool(self.r.photo), self.r.stored, self.r.title), (False, 0, "형광등"))
        self.assertFalse(default_storage.exists(name))
        self.assertEqual(self.manager.post(url).status_code, 303)  # 사진이 없어도 그대로


class IsolationTests(Base):
    def setUp(self):
        super().setUp()
        self.far_hub = open_school(self.far_school, UNTIL, self.far_member)
        self.far_client = self.as_(self.far_user)
        self.far_base = f"/s/{self.far_hub.token}/repairs"
        self.r = self.make()
        self.teacher.post(f"{self.base}/{self.r.key}/edit", {"cat": "facility", "title": "형광등"}, follow=False)

    def test_other_school_member_404_on_this_space(self):
        for path in ("", "/new", f"/{self.r.key}", f"/{self.r.key}/photo", f"/{self.r.key}/edit"):
            self.assertEqual(self.far_client.get(f"{self.base}{path}").status_code, 404, path)
        for path in (f"/{self.r.key}/state", f"/{self.r.key}/delete", f"/{self.r.key}/edit"):
            self.assertEqual(self.far_client.post(f"{self.base}{path}", {"status": "done"}).status_code, 404, path)

    def test_key_from_other_space_404_inside_own_space(self):
        for path in (f"/{self.r.key}", f"/{self.r.key}/photo", f"/{self.r.key}/edit"):
            self.assertEqual(self.far_client.get(f"{self.far_base}{path}").status_code, 404, path)
        for path in (f"/{self.r.key}/state", f"/{self.r.key}/delete", f"/{self.r.key}/edit"):
            self.assertEqual(self.far_client.post(f"{self.far_base}{path}", {"status": "done"}).status_code, 404, path)
        self.r.refresh_from_db()
        self.assertEqual(self.r.status, Repair.Status.RECEIVED)

    def test_list_shows_only_my_space(self):
        self.assertNotContains(self.far_client.get(self.far_base), "형광등")

    def test_designate_member_of_other_space_404(self):
        res = self.far_client.post(f"/s/{self.far_hub.token}/school/members/{self.m5.key}/repair", {"on": "1"})
        self.assertEqual(res.status_code, 404)

    def test_grade_space_404(self):
        lone = School.objects.create(neis_atpt="C10", neis_code="7000001", name="동학년초")
        from spaces.models import Space
        from django.contrib.auth import get_user_model
        grade = Space.objects.create(school=lone, grade=3, name="3학년")
        u = get_user_model().objects.create(username="google:g3", first_name="셋")
        Member.objects.create(space=grade, user=u, name="셋", cls="3-1")
        c = self.as_(u)
        self.assertEqual(c.get(f"/s/{grade.token}/repairs").status_code, 404)
        self.assertEqual(c.get(f"/s/{grade.token}/repairs/new").status_code, 404)
        self.assertEqual(c.post(f"/s/{grade.token}/repairs/new", {"cat": "info", "title": "x"}).status_code, 404)

    def test_anonymous_redirected_to_login(self):
        res = Client().get(f"{self.base}/{self.r.key}/photo")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/start", res["Location"])


class ReencodeTests(Base):
    def test_exif_gps_comment_removed_from_saved_file(self):
        raw = jpeg_bytes(exif=gps_exif(), comment=b"SECRET-COMMENT")
        with Image.open(io.BytesIO(raw)) as check:   # 입력에는 분명히 있다
            self.assertEqual(check.getexif()[0x010F], "SecretCamera")
        self.assertIn(b"SECRET-COMMENT", raw)
        self.assertEqual(self.request(photo=up(raw)).status_code, 302)
        r = Repair.objects.in_space(self.hub).get()
        stored = r.photo.read()
        r.photo.close()
        self.assertEqual(len(stored), r.stored)
        for needle in (b"SecretCamera", b"SECRET-COMMENT", b"Exif"):
            self.assertNotIn(needle, stored)
        with Image.open(io.BytesIO(stored)) as out:
            self.assertEqual(out.format, "JPEG")
            self.assertEqual(len(out.getexif()), 0)
            self.assertEqual(out.getexif().get_ifd(0x8825), {})
            self.assertNotIn("comment", out.info)
            self.assertNotIn("icc_profile", out.info)

    def test_png_text_chunk_removed(self):
        from PIL.PngImagePlugin import PngInfo
        meta = PngInfo()
        meta.add_text("Location", "SECRET-PLACE")
        buf = io.BytesIO()
        Image.new("RGBA", (40, 40), (10, 20, 30, 0)).save(buf, "PNG", pnginfo=meta)
        out = photo.reencode(io.BytesIO(buf.getvalue()))
        self.assertNotIn(b"SECRET-PLACE", out)
        with Image.open(io.BytesIO(out)) as im:
            self.assertEqual((im.format, im.mode), ("JPEG", "RGB"))
            self.assertGreater(im.getpixel((5, 5))[0], 240)  # 투명은 흰 바탕

    def test_orientation_applied_then_dropped(self):
        out = photo.reencode(io.BytesIO(jpeg_bytes(size=(80, 40), exif=gps_exif(orientation=6))))
        with Image.open(io.BytesIO(out)) as im:
            self.assertEqual(im.size, (40, 80))
            self.assertEqual(len(im.getexif()), 0)

    def test_pixel_limit_passes_phone_photos(self):
        for size in ((4000, 3000), (5000, 4000), (5184, 3888)):  # 1200만·2000만·2015만
            with Image.open(io.BytesIO(photo.reencode(io.BytesIO(jpeg_bytes(size=size))))) as im:
                self.assertEqual(max(im.size), 1600)
        for raw in (jpeg_bytes(size=(8000, 6000)), png_header_only(6000, 4000)):  # 4800만·2400만
            with self.assertRaises(photo.BadPhoto) as e:
                photo.reencode(io.BytesIO(raw))
            self.assertIn("2000만", str(e.exception))

    def test_long_side_1600(self):
        for raw in (jpeg_bytes(size=(4000, 2000)), png_bytes(size=(2000, 4000))):
            with Image.open(io.BytesIO(photo.reencode(io.BytesIO(raw)))) as im:
                self.assertEqual(max(im.size), 1600)
                self.assertEqual(min(im.size), 800)

    def test_small_image_not_enlarged(self):
        with Image.open(io.BytesIO(photo.reencode(io.BytesIO(jpeg_bytes(size=(64, 48)))))) as im:
            self.assertEqual(im.size, (64, 48))

    def test_cmyk_and_gray_and_palette(self):
        for mode in ("CMYK", "L"):
            buf = io.BytesIO()
            Image.new(mode, (30, 30)).save(buf, "JPEG")
            with Image.open(io.BytesIO(photo.reencode(io.BytesIO(buf.getvalue())))) as im:
                self.assertEqual(im.mode, "RGB")
        buf = io.BytesIO()
        Image.new("P", (30, 30)).save(buf, "PNG")
        self.assertTrue(photo.reencode(io.BytesIO(buf.getvalue())))

    def test_mpo_from_phone_accepted(self):
        buf = io.BytesIO()
        one = Image.new("RGB", (30, 30), "red")
        one.save(buf, "MPO", save_all=True, append_images=[Image.new("RGB", (30, 30), "blue")])
        self.assertIn(b"MPF", buf.getvalue())
        with Image.open(io.BytesIO(photo.reencode(io.BytesIO(buf.getvalue())))) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertNotIn("mpinfo", im.info)

    def reject(self, data, name="p.jpg"):
        before = SourceFile.used_bytes(self.hub)
        res = self.request(photo=up(data, name))
        self.assertEqual(res.status_code, 400, name)
        self.assertFalse(Repair.objects.in_space(self.hub).exists())
        self.assertEqual(SourceFile.used_bytes(self.hub), before)
        return res

    def test_forged_extension_rejected(self):
        self.assertContains(self.reject(b"<html><script>alert(1)</script></html>"), "JPG·PNG 사진만", status_code=400)
        self.reject(b"%PDF-1.7\n1 0 obj\n", "a.jpg")

    def test_other_formats_rejected_even_if_valid(self):
        for fmt in ("GIF", "BMP", "WEBP", "TIFF"):
            buf = io.BytesIO()
            Image.new("RGB", (20, 20)).save(buf, fmt)
            self.reject(buf.getvalue(), "a.jpg")

    def test_svg_rejected(self):
        self.reject(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "a.jpg")

    def test_heic_header_rejected_with_guidance(self):
        res = self.reject(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "IMG_1.heic")
        self.assertContains(res, "HEIC", status_code=400)

    def test_pixel_bomb_rejected(self):
        res = self.reject(png_header_only(40000, 40000))
        self.assertContains(res, "화소", status_code=400)
        self.reject(png_header_only(7000, 7000))  # 49M: Pillow 경고 미만이어도 우리 상한 초과
        self.reject(png_header_only(100000, 100000))  # Pillow가 먼저 거부하는 크기

    def test_broken_image_rejected(self):
        good = jpeg_bytes(size=(400, 400))
        self.reject(good[:len(good) // 2])
        self.reject(png_bytes()[:30])
        self.reject(b"")
        self.reject(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    def test_too_big_rejected_before_decoding(self):
        with mock.patch.object(photo, "reencode") as enc:
            res = self.reject(b"\xff\xd8" + b"0" * (8 * 1024 * 1024 + 1))
        enc.assert_not_called()
        self.assertContains(res, "8MB", status_code=400)

    def test_values_kept_on_photo_error(self):
        res = self.request(title="문고리", photo=up(b"junk"))
        self.assertContains(res, 'value="문고리"', status_code=400)

    def test_upload_rate_limited(self):
        with mock.patch.object(views, "allow", return_value=False):
            self.assertEqual(self.request(photo=up(jpeg_bytes())).status_code, 429)
        self.assertEqual(self.request().status_code, 302)


class PhotoServeTests(Base):
    def setUp(self):
        super().setUp()
        self.request(photo=up(jpeg_bytes()))
        self.r = Repair.objects.in_space(self.hub).get()
        self.url = f"{self.base}/{self.r.key}/photo"

    def test_member_gets_inline_jpeg_with_safe_headers(self):
        res = self.other.get(self.url)
        self.assertEqual((res.status_code, res["Content-Type"]), (200, "image/jpeg"))
        body = b"".join(res.streaming_content)
        self.assertTrue(body.startswith(b"\xff\xd8"))
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", res["Content-Security-Policy"])
        self.assertIn("private", res["Cache-Control"])
        self.assertNotIn("no-store", res["Cache-Control"])

    def test_no_photo_404_missing_file_404(self):
        bare = self.make()
        self.assertEqual(self.teacher.get(f"{self.base}/{bare.key}/photo").status_code, 404)
        default_storage.delete(self.r.photo.name)
        self.assertEqual(self.teacher.get(self.url).status_code, 404)

    def test_path_has_no_upload_name_and_is_jpg(self):
        self.assertRegex(self.r.photo.name, rf"^repair/{self.hub.pk}/[\w-]+\.jpg$")

    def test_detail_shows_photo(self):
        self.assertContains(self.teacher.get(f"{self.base}/{self.r.key}"), self.url)

    def test_quota_shared(self):
        before = SourceFile.used_bytes(self.hub)
        Repair.objects.filter(pk=self.r.pk).update(stored=self.r.stored + 1000)
        self.assertEqual(SourceFile.used_bytes(self.hub), before + 1000)
        Repair.objects.filter(pk=self.r.pk).update(stored=self.r.stored)
        with override_settings(HWP_SCHOOL_QUOTA=SourceFile.used_bytes(self.hub) + 1):
            res = self.request(title="둘째", photo=up(jpeg_bytes()))
            self.assertContains(res, "저장 공간이 가득", status_code=400)
        with override_settings(HWP_TOTAL_QUOTA=SourceFile.used_bytes() + 1):
            self.assertContains(self.request(title="셋째", photo=up(jpeg_bytes())), "저장 공간이 가득", status_code=400)
        self.assertEqual(Repair.objects.in_space(self.hub).count(), 1)

    def test_delete_removes_file_and_quota(self):
        name, before = self.r.photo.name, SourceFile.used_bytes(self.hub)
        with self.captureOnCommitCallbacks(execute=True):
            self.teacher.post(f"{self.base}/{self.r.key}/delete")
        self.assertFalse(default_storage.exists(name))
        self.assertEqual(SourceFile.used_bytes(self.hub), before - self.r.stored)

    def test_replace_removes_old_file(self):
        name = self.r.photo.name
        new = jpeg_bytes(size=(200, 200))
        with self.captureOnCommitCallbacks(execute=True):
            res = self.teacher.post(f"{self.base}/{self.r.key}/edit", {"cat": "facility", "title": "형광등", "photo": up(new)})
        self.assertEqual(res.status_code, 302)
        self.r.refresh_from_db()
        self.assertFalse(default_storage.exists(name))
        self.assertTrue(default_storage.exists(self.r.photo.name))
        self.assertEqual(self.r.stored, self.r.photo.size)

    def test_remove_photo(self):
        name = self.r.photo.name
        with self.captureOnCommitCallbacks(execute=True):
            self.teacher.post(f"{self.base}/{self.r.key}/edit", {"cat": "facility", "title": "형광등", "remove_photo": "on"})
        self.r.refresh_from_db()
        self.assertFalse(self.r.photo)
        self.assertEqual(self.r.stored, 0)
        self.assertFalse(default_storage.exists(name))

    def test_edit_without_file_keeps_photo(self):
        name = self.r.photo.name
        self.teacher.post(f"{self.base}/{self.r.key}/edit", {"cat": "facility", "title": "새"})
        self.r.refresh_from_db()
        self.assertEqual(self.r.photo.name, name)


class RetentionTests(Base):
    def old(self, days, **kw):
        r = self.make(**kw)
        Repair.objects.filter(pk=r.pk).update(updated_at=timezone.now() - timedelta(days=days))
        return r

    def test_done_90_days_deleted_with_file(self):
        self.request(photo=up(jpeg_bytes()))
        r = Repair.objects.in_space(self.hub).get()
        name = r.photo.name
        Repair.objects.filter(pk=r.pk).update(status=Repair.Status.DONE, updated_at=timezone.now() - timedelta(days=91))
        fresh = self.old(89, status=Repair.Status.DONE)
        open_old = self.old(200, status=Repair.Status.DOING)
        with self.captureOnCommitCallbacks(execute=True):
            n = purge(timezone.now())
        self.assertEqual(n["repairs"], 1)
        self.assertFalse(Repair.objects.filter(pk=r.pk).exists())
        self.assertFalse(default_storage.exists(name))
        self.assertTrue(Repair.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(Repair.objects.filter(pk=open_old.pk).exists())

    def test_unfinished_kept_one_year_at_most(self):
        r = self.old(366, status=Repair.Status.DOING)
        self.assertEqual(purge(timezone.now())["repairs"], 1)
        self.assertFalse(Repair.objects.filter(pk=r.pk).exists())

    def test_dry_run_keeps(self):
        r = self.old(100, status=Repair.Status.DONE)
        self.assertEqual(purge(timezone.now(), dry_run=True)["repairs"], 1)
        self.assertTrue(Repair.objects.filter(pk=r.pk).exists())

    def test_photo_not_treated_as_orphan_but_stray_is(self):
        self.request(photo=up(jpeg_bytes()))
        r = Repair.objects.in_space(self.hub).get()
        stray = default_storage.save(f"repair/{self.hub.pk}/stray.jpg", io.BytesIO(b"x"))
        later = timezone.now() + timedelta(hours=2)
        names = orphan_files(later)
        self.assertIn(stray, names)
        self.assertNotIn(r.photo.name, names)

    def test_status_change_bumps_updated_at(self):
        r = self.old(100)
        self.manager.post(f"{self.base}/{r.key}/state", {"status": "done", "reply": ""})
        r.refresh_from_db()
        self.assertGreater(r.updated_at, timezone.now() - timedelta(minutes=1))
