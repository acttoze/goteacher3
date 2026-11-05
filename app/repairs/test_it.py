"""Q4 쓰기 대화상자, Q5 정보수리방 분리, Q6 업체 링크(사용 기간 없음·항상 복사)."""
import re
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import hash_token
from collect.models import Notification
from hwpfiles.retention import purge
from spaces.merge import open_school
from spaces.models import Member, School

from .models import Repair, VendorAccount
from .test_vendor import MARK_FACILITY, MARK_TITLE, VendorBase

COPY = "업체 링크 복사"
GUIDE = "업체는 학교 밖 정보수리방 화면에서 정보 수리 요청만 확인해요. 학교 안 일정·공지·명부에는 들어올 수 없어요."


class ModalTests(VendorBase):
    def test_list_has_closed_dialog_and_new_opens_it(self):
        for url, cat in ((self.base, "facility"), (self.it, "info")):
            page = self.teacher.get(url).content.decode()
            self.assertRegex(page, r'id="repairDlg" hidden')
            self.assertIn('role="dialog"', page)
            self.assertIn(f'name="cat" value="{cat}"', page)
            self.assertNotIn('type="radio" name="cat"', page)
        page = self.teacher.get(f"{self.base}/new").content.decode()
        self.assertNotRegex(page, r'id="repairDlg" hidden')
        self.assertIn('name="cat" value="facility"', page)

    def test_error_keeps_dialog_open_with_values_on_that_page(self):
        res = self.teacher.post(f"{self.base}/new", {"cat": "info", "title": "", "place": "표식위치칸", "note": "표식메모칸"})
        self.assertEqual(res.status_code, 400)
        html = res.content.decode()
        self.assertNotRegex(html, r'id="repairDlg" hidden')
        self.assertIn('value="표식위치칸"', html)
        self.assertIn('value="표식메모칸"', html)
        self.assertIn("한 줄만 적어 주세요", html)
        self.assertIn('name="cat" value="info"', html)
        self.assertIn("<h1>정보수리방</h1>", html)

    def test_bad_photo_keeps_dialog_open(self):
        bad = SimpleUploadedFile("a.jpg", b"not image", content_type="image/jpeg")
        res = self.teacher.post(f"{self.base}/new", {"cat": "facility", "title": "표식제목칸", "photo": bad})
        self.assertEqual(res.status_code, 400)
        html = res.content.decode()
        self.assertNotRegex(html, r'id="repairDlg" hidden')
        self.assertIn('value="표식제목칸"', html)

    def test_server_validates_cat(self):
        res = self.teacher.post(f"{self.base}/new", {"cat": "hack", "title": "표식나쁜값"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Repair.objects.filter(title="표식나쁜값").exists())
        self.assertContains(res, "시설과 정보 중에서", status_code=400)

    def test_form_error_does_not_open_vendor_dialog(self):
        self.issued()
        html = self.manager.post(f"{self.base}/new", {"cat": "info", "title": ""}).content.decode()
        self.assertRegex(html, r'id="vendorSwap" hidden')

    def test_success_returns_to_its_list(self):
        for cat, url in (("facility", self.base), ("info", self.it)):
            res = self.teacher.post(f"{self.base}/new", {"cat": cat, "title": f"{cat}표식"})
            self.assertEqual((res.status_code, res["Location"]), (302, url))
            self.assertContains(self.teacher.get(url), f"{cat}표식")

    def test_edit_keeps_page_and_category_choice(self):
        html = self.teacher.get(f"{self.base}/{self.info.key}/edit").content.decode()
        self.assertIn('type="radio" name="cat" value="info" checked', html)
        self.assertNotIn('class="scrim"', html)
        self.assertIn(f'href="{self.it}"', html)

    def test_expired_has_no_dialog(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assertNotContains(self.teacher.get(self.it), "repairDlg")


class RoomTests(VendorBase):
    def test_rooms_hold_only_their_category_including_old_requests(self):
        Repair.objects.filter(pk=self.info.pk).update(created_at=timezone.now() - timedelta(days=200))
        room, tab = self.teacher.get(self.it), self.teacher.get(self.base)
        self.assertContains(room, MARK_TITLE)
        self.assertNotContains(room, MARK_FACILITY)
        self.assertContains(tab, MARK_FACILITY)
        self.assertNotContains(tab, MARK_TITLE)

    def test_tab_points_to_room_and_counts_facility_only(self):
        tab = self.teacher.get(self.base)
        self.assertContains(tab, "PC·전자칠판·네트워크 고장은 학교 밖 정보수리방으로")
        self.assertContains(tab, f'href="{self.it}"')
        self.assertNotContains(tab, "vendorCard")
        board = self.teacher.get(f"/s/{self.hub.token}/board").content.decode()
        for html in (tab.content.decode(), board):
            self.assertIn('수리 요청 <span class="c num">1</span>', html)

    def test_facility_not_at_it_detail(self):
        self.assertEqual(self.teacher.get(f"{self.it}/{self.facility.key}").status_code, 404)
        self.assertEqual(self.teacher.get(f"{self.it}/{self.info.key}").status_code, 200)

    def test_vendor_sees_no_facility(self):
        c = self.entered()
        page = c.get("/v/")
        self.assertContains(page, "<h1>정보수리방</h1>")
        self.assertContains(page, MARK_TITLE)
        self.assertNotContains(page, MARK_FACILITY)
        self.assertEqual(c.post(f"/v/r/{self.facility.key}/state", {"status": "done", "reply": ""}).status_code, 404)

    def test_other_school_gets_404(self):
        far = open_school(self.far_school, timezone.localdate() + timedelta(days=9), Member.objects.get(space__school=self.far_school, user=self.far_user))
        self.assertEqual(self.teacher.get(f"/s/{far.token}/it").status_code, 404)
        far_member = Member.objects.in_space(far).get(user=self.far_user)
        theirs = Repair.objects.create(space=far, by=far_member, by_name="먼사람", cat="info", title="먼요청")
        self.assertEqual(self.teacher.get(f"{self.it}/{theirs.key}").status_code, 404)
        self.assertNotContains(self.teacher.get(self.it), "먼요청")

    def test_anonymous_cannot_enter_room(self):
        self.assertNotEqual(Client().get(self.it).status_code, 200)

    def test_grade_space_has_no_room(self):
        grade = Member.objects.get(space__school=self.far_school, user=self.far_user).space
        self.assertFalse(grade.is_school)
        c = self.as_(self.far_user)
        self.assertNotEqual(c.get(f"/s/{grade.token}/it").status_code, 200)

    def test_bell_and_back_links(self):
        self.teacher.post(f"{self.base}/new", {"cat": "info", "title": "정보종"})
        self.teacher.post(f"{self.base}/new", {"cat": "facility", "title": "시설종"})
        info = Repair.objects.get(title="정보종")
        fac = Repair.objects.get(title="시설종")
        bells = Notification.objects.in_space(self.hub)
        self.assertEqual(bells.filter(repair=info).first().link, f"{self.it}/{info.key}")
        self.assertEqual(bells.filter(repair=fac).first().link, f"{self.base}/{fac.key}")
        for url in (f"{self.it}/{info.key}", f"{self.base}/{info.key}"):
            self.assertContains(self.teacher.get(url), "← 정보수리방")
        self.assertContains(self.teacher.get(f"{self.base}/{fac.key}"), "← 수리 요청")
        self.manager.post(f"{self.base}/{info.key}/state", {"status": "visit", "reply": ""})
        self.assertEqual(bells.filter(repair=info, member=self.m4b).latest("pk").link, f"{self.it}/{info.key}")
        res = self.manager.post(f"{self.base}/{info.key}/delete")
        self.assertEqual(res["Location"], self.it)

    def test_moving_category_moves_bell_links(self):
        self.teacher.post(f"{self.base}/new", {"cat": "info", "title": "옮길것"})
        r = Repair.objects.get(title="옮길것")
        self.assertEqual(self.teacher.post(f"{self.base}/{r.key}/edit", {"cat": "facility", "title": "옮길것"})["Location"], f"{self.base}/{r.key}")
        for link in Notification.objects.in_space(self.hub).filter(repair=r).values_list("link", flat=True):
            self.assertEqual(link, f"{self.base}/{r.key}")

    def test_audit_link_goes_to_room(self):
        self.issued()
        self.assertEqual(Notification.objects.in_space(self.hub).filter(kind=Notification.Kind.VENDOR).first().link, self.it)

    def test_preview_and_log_go_back_to_room(self):
        self.issued()
        for url in (f"{self.vurl}/preview", f"{self.vurl}/log"):
            self.assertContains(self.manager.get(url), "← 정보수리방")


class LinkTests(VendorBase):
    def link_of(self, client=None):
        res = (client or self.manager).post(f"{self.vurl}/link")
        self.assertEqual(res.status_code, 200)
        return re.fullmatch(r"https?://[^/]+(/v/[^/]+)", res.content.decode()).group(1)

    def test_same_link_after_reload_and_it_works(self):
        _, link = self.issued()
        self.assertEqual(self.link_of(), link)
        self.assertEqual(self.link_of(), link)
        page = self.manager.get(self.it)
        self.assertContains(page, COPY)
        self.assertContains(page, GUIDE)
        self.assertNotContains(page, "한 번만")
        self.assertEqual(Client().get(link).status_code, 302)
        self.assertIn("no-cache", page["Cache-Control"])
        self.assertIn("no-store", page["Cache-Control"])

    def test_no_until_field_or_expiry(self):
        account, link = self.issued()
        self.assertFalse(hasattr(account, "until"))
        self.assertNotContains(self.manager.get(self.it), "사용 기간")
        self.assertNotContains(self.manager.get(self.it), 'name="until"')
        with mock.patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(days=20)):
            self.assertEqual(Client().get(link).status_code, 302)

    def test_reissue_cuts_old_link_and_shows_new(self):
        _, old = self.issued()
        c = self.entered(old)
        _, new = self.issued()
        self.assertNotEqual(old, new)
        self.assertEqual(self.link_of(), new)
        self.closed(Client().get(old))
        self.closed(c.get("/v/"))

    def test_ordinary_member_response_has_no_link(self):
        account, link = self.issued()
        token = link.rsplit("/", 1)[1]
        for url in (self.it, f"{self.it}/{self.info.key}", self.base, f"{self.base}/new"):
            for c in (self.teacher, self.other):
                res = c.get(url).content.decode()
                self.assertNotIn(token, res, url)
                self.assertNotIn(account.nonce, res, url)
                self.assertNotIn("data-link-copy", res, url)

    def test_handler_gets_link_too(self):
        _, link = self.issued()
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.assertEqual(self.link_of(self.other), link)

    def test_db_values_alone_make_no_token(self):
        account, link = self.issued()
        account.refresh_from_db()
        token = link.rsplit("/", 1)[1]
        self.assertTrue(account.nonce)
        self.assertNotIn(token, (account.nonce, account.token_hash, account.cookie_hash))
        self.assertEqual(hash_token(token), account.token_hash)
        with override_settings(SECRET_KEY="다른 비밀 값"):  # 비밀 없이 DB 값만으로는 같은 토큰이 나오지 않는다
            self.assertNotEqual(VendorAccount._token(account.nonce), token)

    def test_changed_secret_asks_for_reissue_not_wrong_link(self):
        self.issued()
        with override_settings(SECRET_KEY="다른 비밀 값"):
            self.assertEqual(VendorAccount.objects.in_space(self.hub).get().link_token(), "")

    def test_old_account_without_nonce_asks_for_reissue(self):
        account, link = self.issued()
        VendorAccount.objects.filter(pk=account.pk).update(nonce="")
        page = self.manager.get(self.it)
        self.assertNotContains(page, COPY)
        self.assertNotContains(page, link)
        self.assertContains(page, "새 링크를 발급해")
        self.assertEqual(self.manager.post(f"{self.vurl}/link").status_code, 404)
        self.assertEqual(Client().get(link).status_code, 302)  # 이미 나간 링크는 그대로 들어온다

    def test_closed_school_shows_no_copy(self):
        _, link = self.issued()
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        page = self.manager.get(self.it)
        self.assertNotContains(page, COPY)
        self.assertNotContains(page, link)
        self.assertEqual(self.manager.post(f"{self.vurl}/link").status_code, 404)
        self.closed(Client().get(link))

    def test_revoke_removes_link(self):
        _, link = self.issued()
        self.manager.post(f"{self.vurl}/revoke")
        self.assertEqual(self.manager.post(f"{self.vurl}/link").status_code, 404)
        self.closed(Client().get(link))

    def test_retention_counts_only_revoked(self):
        live, _ = self.issued()
        VendorAccount.objects.filter(pk=live.pk).update(created_at=timezone.now() - timedelta(days=800))
        purge(timezone.now())
        self.assertTrue(VendorAccount.objects.filter(pk=live.pk).exists())
