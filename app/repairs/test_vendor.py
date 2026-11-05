"""K2 업체 계정: 비밀 링크 입장·기기 쿠키·재발급·해제·만료, 교내 주소 전수 차단, 이름 비공개, 관리 화면 권한."""
import re
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from collect.models import Notification
from config.logsafe import _mask
from hwpfiles.retention import purge
from notices.models import Notice
from spaces.merge import open_school
from spaces.models import Member, MemberLog, School

from .models import Repair, VendorAccess, VendorAccount
from .tests import Base, jpeg_bytes, up
from .vendor import COOKIE

User = get_user_model()
MARK_NAME = "표식요청자"
MARK_TITLE = "표식제목전자칠판"
MARK_NOTE = "표식메모오전만"
MARK_FACILITY = "표식시설뒷문"
MARK_NOTICE = "표식공지제목"
LATER = timezone.localdate() + timedelta(days=100)
ALL = re.compile(r"<(?:\w+:)?(\w+)>")


def walk(patterns, prefix=""):
    """include까지 들어가 모든 URL 패턴 문자열을 돌려준다. 새 주소가 생기면 저절로 포함된다."""
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern)


class VendorBase(Base):
    def setUp(self):
        super().setUp()
        self.info = self.make(cat="info", title=MARK_TITLE, note=MARK_NOTE, place="4-2 교실", urgent=True)
        Repair.objects.filter(pk=self.info.pk).update(by_name=MARK_NAME)
        self.facility = self.make(cat="facility", title=MARK_FACILITY)
        Repair.objects.filter(pk=self.facility.pk).update(by_name=MARK_NAME)
        self.vurl = f"{self.base}/vendor"
        self.it = f"/s/{self.hub.token}/it"

    def issue(self, client=None, **data):
        form = {"name": "한빛정보", "contact_name": "강업체", **data}
        return (client or self.manager).post(self.vurl, form)

    def issued(self, keep_visible=False, **data):
        """발급하고 (계정, 링크 경로)를 돌려준다. 앞서 만든 표본 요청이 보이도록 visible_from을 당긴다(발급 시각 규칙은 VisibleFromTests)."""
        res = self.issue(**data)
        self.assertEqual(res.status_code, 200)
        account = VendorAccount.objects.in_space(self.hub).get(revoked_at__isnull=True)
        link = f"/v/{account.link_token()}"  # 복사 기록을 남기지 않고 만든다. 복사 단추는 LinkCopyTests
        if not keep_visible:
            VendorAccount.objects.filter(pk=account.pk).update(visible_from=timezone.now() - timedelta(days=1))
            account.refresh_from_db()
        return account, link

    def entered(self, link=None):
        """링크로 들어온 업체 브라우저."""
        if link is None:
            _, link = self.issued()
        c = Client()
        res = c.get(link)
        self.assertEqual((res.status_code, res["Location"]), (302, "/v/"))
        return c

    def closed(self, res):
        self.assertEqual(res.status_code, 404)
        self.assertContains(res, "들어올 수 없어요", status_code=404)
        self.assertNotContains(res, MARK_TITLE, status_code=404)
        self.assertNotContains(res, self.school.name, status_code=404)


class EntryTests(VendorBase):
    def test_link_sets_cookie_and_redirects_without_token(self):
        _, link = self.issued()
        res = Client().get(link)
        self.assertEqual((res.status_code, res["Location"]), (302, "/v/"))
        ck = res.cookies[COOKIE]
        self.assertTrue(ck["httponly"])
        self.assertEqual(ck["samesite"], "Lax")
        self.assertEqual(ck["path"], "/v/")
        self.assertEqual(int(ck["max-age"]), 86400 * 30)
        self.assertNotIn(link.split("/")[-1], ck.value)

    @override_settings(SITE_URL="https://t.goteacher.xyz")
    def test_cookie_secure_on_https(self):
        _, link = self.issued()
        self.assertTrue(Client().get(link).cookies[COOKIE]["secure"])

    def test_headers_on_every_vendor_response(self):
        _, link = self.issued()
        c = Client()
        for res in (c.get(link), c.get("/v/"), Client().get("/v/"), Client().get("/v/nope"),
                    c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": ""})):
            self.assertIn("noindex", res["X-Robots-Tag"])
            self.assertIn("no-store", res["Cache-Control"])

    def test_referrer_only_token_urls_are_no_referrer(self):
        """토큰이 든 주소만 no-referrer. /v/ 화면은 same-origin이어야 폼 제출의 Origin이 null이 되지 않는다(CSRF)."""
        _, link = self.issued()
        c = Client()
        self.assertEqual(c.get(link)["Referrer-Policy"], "no-referrer")
        self.assertEqual(Client().get("/v/nope")["Referrer-Policy"], "no-referrer")
        self.assertEqual(c.get("/v/")["Referrer-Policy"], "same-origin")
        self.assertEqual(c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": ""})["Referrer-Policy"], "same-origin")

    def test_real_browser_origin_passes_csrf(self):
        _, link = self.issued()
        c = Client(enforce_csrf_checks=True)
        c.get(link)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', c.get("/v/").content.decode()).group(1)
        res = c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": "", "csrfmiddlewaretoken": token},
                     headers={"Origin": "http://testserver"})
        self.assertEqual(res.status_code, 303)

    def test_cookie_refreshed_on_open(self):
        _, link = self.issued()
        c = self.entered(link)
        self.assertEqual(int(c.get("/v/").cookies[COOKIE]["max-age"]), 86400 * 30)

    def test_leave_clears_cookie_only_on_this_browser(self):
        _, link = self.issued()
        c = self.entered(link)
        self.assertContains(c.get("/v/"), "/v/out")
        res = c.post("/v/out")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.cookies[COOKIE]["max-age"], 0)
        self.assertEqual(res.cookies[COOKIE]["path"], "/v/")
        self.assertEqual(c.get("/v/out").status_code, 405)
        self.closed(c.get("/v/"))
        self.assertContains(self.entered(link).get("/v/"), MARK_TITLE)

    def test_no_user_member_or_session_is_made(self):
        _, link = self.issued()
        users, members = User.objects.count(), Member.objects.count()
        c = self.entered(link)
        c.get("/v/")
        c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": ""})
        self.assertEqual((User.objects.count(), Member.objects.count()), (users, members))
        self.assertNotIn("sessionid", c.cookies)

    def test_token_and_cookie_only_hashed_in_db(self):
        account, link = self.issued()
        raw = link.split("/")[-1]
        c = Client()
        c.get(link)
        cookie = c.cookies[COOKIE].value
        self.assertGreaterEqual(len(raw), 43)  # token_urlsafe(32)
        for f in VendorAccount._meta.concrete_fields:
            value = str(getattr(account, f.attname))
            self.assertNotIn(raw, value, f.name)
            self.assertNotIn(cookie, value, f.name)

    def test_link_shown_only_once(self):
        _, link = self.issued()
        self.assertNotContains(self.manager.get(self.base), link)
        self.assertNotContains(self.manager.get(self.base), "/v/")

    def test_unknown_token_and_no_cookie_same_closed_page(self):
        self.closed(Client().get("/v/" + "x" * 43))
        self.closed(Client().get("/v/"))
        c = Client()
        c.cookies[COOKIE] = "x" * 64
        self.closed(c.get("/v/"))

    def test_entry_is_rate_limited(self):
        caches["ratelimit"].clear()
        c = Client()
        codes = [c.get("/v/" + "x" * 43).status_code for _ in range(40)]
        self.assertEqual(codes[0], 404)
        self.assertEqual(codes[-1], 429)

    def test_log_masks_token(self):
        self.assertEqual(_mask("Internal Server Error: /v/abcDEF"), "Internal Server Error: /v/***")


class CutTests(VendorBase):
    def test_reissue_cuts_old_link_and_cookie(self):
        old, link = self.issued()
        c = self.entered(link)
        self.assertContains(c.get("/v/"), MARK_TITLE)
        new, link2 = self.issued(contact_name="새담당")
        self.assertNotEqual(link, link2)
        self.assertNotEqual(old.pk, new.pk)
        old.refresh_from_db()
        self.assertIsNotNone(old.revoked_at)
        self.closed(c.get("/v/"))
        self.closed(c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}))
        self.closed(Client().get(link))
        self.assertContains(self.entered(link2).get("/v/"), MARK_TITLE)

    def test_revoke_cuts(self):
        _, link = self.issued()
        c = self.entered(link)
        self.assertEqual(self.manager.post(f"{self.vurl}/revoke").status_code, 200)
        self.closed(c.get("/v/"))
        self.closed(Client().get(link))
        self.assertEqual(VendorAccount.objects.in_space(self.hub).filter(revoked_at__isnull=True).count(), 0)

    def test_plan_expired_cuts_everything(self):
        _, link = self.issued()
        c = self.entered(link)
        self.school.paid_until = timezone.localdate() - timedelta(days=1)
        self.school.save()
        self.closed(c.get("/v/"))
        self.closed(Client().get(link))
        self.closed(c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}))
        self.closed(c.get(f"/v/r/{self.info.key}/photo"))
        self.info.refresh_from_db()
        self.assertEqual(self.info.status, Repair.Status.RECEIVED)

    def test_plan_free_cuts(self):
        _, link = self.issued()
        c = self.entered(link)
        self.school.plan = School.Plan.FREE
        self.school.save()
        self.closed(c.get("/v/"))

    def test_one_live_account_per_school_in_db(self):
        account, _ = self.issued()
        with self.assertRaises(IntegrityError), transaction.atomic():
            VendorAccount.objects.create(space=self.hub, name="둘째", contact_name="", token_hash="a" * 64, cookie_hash="b" * 64)
        VendorAccount.objects.filter(pk=account.pk).update(revoked_at=timezone.now())
        VendorAccount.objects.create(space=self.hub, name="둘째", contact_name="", token_hash="a" * 64, cookie_hash="b" * 64)


class ScreenTests(VendorBase):
    def setUp(self):
        super().setUp()
        self.c = self.entered()

    def test_only_info_requests_and_school_name(self):
        page = self.c.get("/v/")
        self.assertContains(page, self.school.name)
        self.assertContains(page, MARK_TITLE)
        self.assertContains(page, MARK_NOTE)
        self.assertContains(page, "4-2 교실")
        self.assertContains(page, "급함")
        self.assertNotContains(page, MARK_FACILITY)

    def test_no_requester_name_anywhere(self):
        for name in (MARK_NAME, self.m4b.name, self.m4.name):
            self.assertNotContains(self.c.get("/v/"), name)
        res = self.c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": "내일 가요"}, headers={"HX-Request": "true"})
        self.assertEqual(res.status_code, 200)
        for name in (MARK_NAME, self.m4b.name):
            self.assertNotContains(res, name)

    def test_school_data_not_on_screen(self):
        page = self.c.get("/v/").content.decode()
        for word in (f"/s/{self.hub.token}", "일정", "명부", "취합"):
            self.assertNotIn(word, page, word)
        self.assertNotIn("sessionid", self.c.cookies)

    def test_state_changes_and_rings_requester_without_vendor_name(self):
        res = self.c.post(f"/v/r/{self.info.key}/state", {"status": "visit", "reply": "내일 오전 갑니다"})
        self.assertEqual((res.status_code, res["Location"]), (303, "/v/"))
        self.info.refresh_from_db()
        self.assertEqual((self.info.status, self.info.reply), (Repair.Status.VISIT, "내일 오전 갑니다"))
        bell = Notification.objects.in_space(self.hub).get(member=self.m4b, kind=Notification.Kind.REPAIR_UP)
        self.assertIn("방문 예정", bell.text)

    def test_bad_state_refused(self):
        url = f"/v/r/{self.info.key}/state"
        self.assertEqual(self.c.post(url, {"status": "nope", "reply": ""}).status_code, 400)
        self.assertEqual(self.c.post(url, {"status": "done", "reply": "가" * 501}).status_code, 400)
        self.info.refresh_from_db()
        self.assertEqual(self.info.status, Repair.Status.RECEIVED)

    def test_facility_key_404_state_and_photo(self):
        Repair.objects.filter(pk=self.facility.pk).update(photo="repair/x/y.jpg")
        self.assertEqual(self.c.post(f"/v/r/{self.facility.key}/state", {"status": "done", "reply": ""}).status_code, 404)
        self.assertEqual(self.c.get(f"/v/r/{self.facility.key}/photo").status_code, 404)
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.status, Repair.Status.RECEIVED)

    def test_other_school_key_404(self):
        far_hub = open_school(self.far_school, LATER, Member.objects.get(space__school=self.far_school, user=self.far_user))
        far_member = Member.objects.in_space(far_hub).get(user=self.far_user)
        far = Repair.objects.create(space=far_hub, by=far_member, by_name="먼사람", cat="info", title="먼학교요청")
        self.assertEqual(self.c.post(f"/v/r/{far.key}/state", {"status": "done", "reply": ""}).status_code, 404)
        self.assertEqual(self.c.get(f"/v/r/{far.key}/photo").status_code, 404)
        self.assertNotContains(self.c.get("/v/"), "먼학교요청")
        far.refresh_from_db()
        self.assertEqual(far.status, Repair.Status.RECEIVED)

    def test_photo_of_info_served_without_cache(self):
        self.request(self.teacher, cat="info", title="사진요청", photo=up(jpeg_bytes()))
        r = Repair.objects.in_space(self.hub).get(title="사진요청")
        page = self.c.get("/v/")
        self.assertContains(page, f"/v/r/{r.key}/photo")
        self.assertNotContains(page, f"/repairs/{r.key}/photo")
        res = self.c.get(f"/v/r/{r.key}/photo")
        self.assertEqual((res.status_code, res["Content-Type"]), (200, "image/jpeg"))
        b"".join(res.streaming_content)
        self.assertIn("no-store", res["Cache-Control"])
        self.assertTrue(VendorAccess.objects.filter(action="photo", repair_key=r.key).exists())
        self.assertContains(self.manager.get(f"{self.vurl}/log"), "사진 열어 봄 · 사진요청")
        self.assertEqual(Client().get(f"/v/r/{r.key}/photo").status_code, 404)

    def test_deleted_request_while_screen_open(self):
        key = self.info.key
        self.info.delete()
        res = self.c.post(f"/v/r/{key}/state", {"status": "done", "reply": ""})
        self.assertEqual(res.status_code, 404)
        self.assertIn("noindex", res["X-Robots-Tag"])
        self.assertIn("no-store", res["Cache-Control"])
        res = self.c.post(f"/v/r/{key}/state", {"status": "done", "reply": ""}, headers={"HX-Request": "true"})
        self.assertEqual((res.status_code, res["HX-Refresh"]), (204, "true"))

    def test_csrf_required_with_vendor_cookie(self):
        c = Client(enforce_csrf_checks=True)
        c.cookies[COOKIE] = self.c.cookies[COOKIE].value
        url = f"/v/r/{self.info.key}/state"
        self.assertEqual(c.post(url, {"status": "done", "reply": ""}).status_code, 403)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', c.get("/v/").content.decode()).group(1)
        self.assertEqual(c.post(url, {"status": "done", "reply": "", "csrfmiddlewaretoken": token}).status_code, 303)

    def test_get_cannot_change(self):
        self.assertEqual(self.c.get(f"/v/r/{self.info.key}/state?status=done").status_code, 405)

    def test_access_log_written_without_ip(self):
        VendorAccess.objects.all().delete()
        VendorAccount.objects.update(last_seen_at=None)
        self.c.get("/v/")
        self.c.get("/v/")  # 곧바로 다시 열면 한 건으로 친다
        self.c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": ""})
        self.c.post(f"/v/r/{self.info.key}/state", {"status": "visit", "reply": ""})  # 화면 열기 말고는 빼지 않는다
        self.assertEqual(sorted(VendorAccess.objects.values_list("action", "repair_key")),
                         [("state", self.info.key), ("state", self.info.key), ("view", "")])
        names = {f.name for f in VendorAccess._meta.get_fields()}
        self.assertEqual(names, {"id", "account", "at", "action", "repair_key"})  # IP 없음
        self.assertIsNotNone(VendorAccount.objects.get(revoked_at__isnull=True).last_seen_at)

    def test_queries_do_not_grow_with_rows(self):
        with CaptureQueriesContext(connection) as few:
            self.c.get("/v/")
        for i in range(10):
            self.make(cat="info", title=f"추가 {i}")
        with CaptureQueriesContext(connection) as many:
            self.c.get("/v/")
        self.assertEqual(len(few), len(many))


class VisibleFromTests(VendorBase):
    """발급 전 요청은 업체에 안 보인다. 목록·사진·상태 변경 모두."""

    def fresh(self):
        _, link = self.issued(keep_visible=True)
        return self.entered(link)

    def test_request_before_issue_hidden_everywhere(self):
        Repair.objects.filter(pk=self.info.pk).update(photo="repair/x/y.jpg")
        c = self.fresh()
        self.assertNotContains(c.get("/v/"), MARK_TITLE)
        self.assertEqual(c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}).status_code, 404)
        self.assertEqual(c.get(f"/v/r/{self.info.key}/photo").status_code, 404)
        self.info.refresh_from_db()
        self.assertEqual(self.info.status, Repair.Status.RECEIVED)
        self.assertNotContains(self.manager.get(f"{self.vurl}/preview"), MARK_TITLE)

    def test_request_after_issue_visible(self):
        c = self.fresh()
        self.request(self.teacher, cat="info", title="발급뒤요청")
        self.assertContains(c.get("/v/"), "발급뒤요청")

    def test_old_request_recategorized_to_info_stays_hidden_but_warning_kept(self):
        c = self.fresh()
        self.assertEqual(self.teacher.post(f"{self.base}/{self.facility.key}/edit", {"cat": "info", "title": MARK_FACILITY}).status_code, 302)
        self.assertNotContains(c.get("/v/"), MARK_FACILITY)
        self.assertContains(self.teacher.get(f"{self.base}/{self.facility.key}/edit"), "외부 업체(한빛정보)가 볼 수 있어요")

    def test_old_request_desk_stays_school(self):
        self.fresh()
        self.assertNotContains(self.teacher.get(f"{self.base}/{self.info.key}"), "한빛정보 (계약 업체)")

    def test_reissue_inherits_and_new_issue_after_revoke_resets(self):
        first, _ = self.issued(keep_visible=True)
        second, _ = self.issued(keep_visible=True, contact_name="새담당")
        self.assertEqual(second.visible_from, first.visible_from)
        self.manager.post(f"{self.vurl}/revoke")
        third, _ = self.issued(keep_visible=True)
        self.assertGreater(third.visible_from, first.visible_from)

    def test_card_says_only_after_this_time(self):
        self.assertContains(self.manager.get(self.it), "링크를 만든 시각 뒤에 올라온")
        self.issued(keep_visible=True)
        self.assertContains(self.manager.get(self.it), "이 시각 뒤에 올라온 정보 수리 요청만 보여요")


class BellAndCountTests(VendorBase):
    def setUp(self):
        super().setUp()
        self.c = self.entered()
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.bell = Notification.objects.create(space=self.hub, member=self.m5, repair=self.info, kind=Notification.Kind.REPAIR,
                                                day=timezone.localdate(), text="요청 옴", link="/x")

    def post(self, status, reply=""):
        return self.c.post(f"/v/r/{self.info.key}/state", {"status": status, "reply": reply})

    def test_vendor_does_not_mark_handler_bell_read(self):
        self.post("doing")
        self.bell.refresh_from_db()
        self.assertFalse(self.bell.read)

    def test_staff_still_marks_handler_bell_read(self):
        self.manager.post(f"{self.base}/{self.info.key}/state", {"status": "doing", "reply": ""})
        self.bell.refresh_from_db()
        self.assertTrue(self.bell.read)

    def test_done_by_vendor_rings_handler_once_requester_once(self):
        up = Notification.objects.in_space(self.hub).filter(kind=Notification.Kind.REPAIR_UP)
        self.post("doing")
        self.assertFalse(up.filter(member=self.m5).exists())
        self.post("done")
        self.assertEqual(up.get(member=self.m5).text, f"{MARK_TITLE} — 업체가 완료로 바꿨어요")
        self.assertIn("완료", up.get(member=self.m4b).text)
        self.post("doing")
        self.post("done")  # 안 읽은 건 쌓지 않는다
        self.assertEqual(up.filter(member=self.m5).count(), 1)

    def test_requester_who_is_handler_gets_one(self):
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=True)
        self.post("done")
        self.assertEqual(Notification.objects.in_space(self.hub).filter(kind=Notification.Kind.REPAIR_UP, member=self.m4b).count(), 1)

    def test_open_count_updated_by_htmx(self):
        self.make(cat="info", title="둘째")
        self.assertRegex(self.c.get("/v/").content.decode(), r'id="vOpen">2<')
        res = self.c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}, headers={"HX-Request": "true"})
        self.assertRegex(res.content.decode(), r'id="vOpen" hx-swap-oob="true">1<')


class MaskTests(VendorBase):
    def setUp(self):
        super().setUp()
        Repair.objects.filter(pk=self.info.pk).update(note=f"{self.m5.name} 선생님 반, {MARK_NAME} 자리 옆", reply=f"{self.m4.name} 선생님께 전달함")
        self.c = self.entered()

    def test_names_in_note_and_reply_masked(self):
        for name in (self.m5.name, self.m4.name, MARK_NAME):
            self.assertNotContains(self.c.get("/v/"), name)
        for page in (self.c.get("/v/"), self.manager.get(f"{self.vurl}/preview")):  # 미리보기 화면 틀에는 내 이름이 있으니 문구로 본다
            self.assertContains(page, "○○○ 선생님 반, ○○○ 자리 옆")
            self.assertContains(page, "○○○ 선생님께 전달함")

    def test_masked_reply_coming_back_keeps_original(self):
        self.c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": "○○○ 선생님께 전달함"})
        self.info.refresh_from_db()
        self.assertEqual((self.info.reply, self.info.reply_vendor), (f"{self.m4.name} 선생님께 전달함", False))

    def test_htmx_row_masked(self):
        res = self.c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": "○○○ 선생님께 전달함"}, headers={"HX-Request": "true"})
        self.assertNotContains(res, self.m4.name)
        self.assertNotContains(res, self.m5.name)

    def test_school_screen_keeps_original(self):
        self.assertContains(self.manager.get(f"{self.base}/{self.info.key}"), f"{self.m5.name} 선생님 반")


class WallTests(VendorBase):
    """업체 쿠키로 교내 어떤 주소도 안 열린다. config/urls.py를 그대로 돌아 새 주소도 저절로 포함한다."""

    def setUp(self):
        super().setUp()
        self.notice = Notice.objects.create(space=self.hub, by=self.m4, by_name=self.m4.name, title=MARK_NOTICE)
        self.c = self.entered()
        self.values = {
            "space_token": self.hub.token, "key": self.info.key, "link_token": self.c4.link_token,
            "token": self.notice.key, "invite_token": self.hub.invite_token, "slug": "none", "v": "1",
        }
        patterns = sorted(set(walk(get_resolver().url_patterns)))
        self.paths = ["/" + ALL.sub(lambda m: self.values.get(m.group(1), self.info.key), p) for p in patterns
                      if not p.startswith("v/")]

    def test_walk_really_covers_app_urls(self):
        for must in (f"/s/{self.hub.token}/school", f"/s/{self.hub.token}/repairs/vendor", f"/s/{self.hub.token}/repairs/vendor/preview",
                     f"/s/{self.hub.token}/c/", f"/s/{self.hub.token}/bell", f"/n/{self.notice.key}"):
            self.assertIn(must, self.paths)
        self.assertGreater(len([p for p in self.paths if p.startswith("/s/")]), 70)

    def assert_no_marks(self, res, path):
        body = b"".join(res.streaming_content) if res.streaming else res.content
        for mark in (MARK_NAME, MARK_TITLE, MARK_NOTE, MARK_FACILITY, MARK_NOTICE):
            self.assertNotIn(mark, body.decode(errors="ignore"), path)

    def test_school_urls_closed_to_vendor_cookie(self):
        for path in [p for p in self.paths if p.startswith("/s/")]:
            for send in (self.c.get, self.c.post):
                res = send(path)
                self.assertIn(res.status_code, (302, 404), path)
                if res.status_code == 302:
                    self.assertTrue(res["Location"].startswith("/start"), path)
                self.assert_no_marks(res, path)
            res = self.c.get(path, headers={"HX-Request": "true"})
            self.assertIn(res.status_code, (204, 404), path)

    def test_other_urls_give_cookie_nothing_more_than_anonymous(self):
        anon = Client()
        for path in [p for p in self.paths if not p.startswith("/s/")]:
            for method in ("get", "post"):
                a, b = getattr(anon, method)(path), getattr(self.c, method)(path)
                self.assertEqual(a.status_code, b.status_code, path)
                self.assert_no_marks(b, path)

    def test_cookie_alone_never_becomes_login(self):
        self.assertFalse(self.c.get("/").wsgi_request.user.is_authenticated)
        self.assertNotIn("_auth_user_id", self.c.session)


class MixTests(VendorBase):
    """교내 회원 브라우저에 업체 쿠키가 같이 있어도 섞이지 않는다."""

    def test_member_of_other_school_with_vendor_cookie_still_404(self):
        c = self.entered()
        c.force_login(self.far_user)
        for path in (f"/s/{self.hub.token}/school", f"{self.base}", f"{self.base}/{self.info.key}", f"{self.base}/vendor/preview"):
            self.assertEqual(c.get(path).status_code, 404, path)
        self.assertContains(c.get("/v/"), MARK_TITLE)  # 업체 화면은 업체 쿠키의 학교

    def test_plain_member_with_vendor_cookie_gets_no_manager_rights(self):
        c = self.entered()
        c.force_login(self.u4b)
        self.assertEqual(c.post(self.vurl, {"name": "x", "contact_name": "y"}).status_code, 403)
        self.assertEqual(c.post(f"{self.vurl}/revoke").status_code, 403)
        self.assertEqual(c.get(f"{self.vurl}/log").status_code, 403)
        self.assertEqual(c.post(f"{self.base}/{self.info.key}/state", {"status": "done", "reply": ""}).status_code, 403)

    def test_member_session_alone_cannot_open_vendor_screen(self):
        self.issued()
        self.closed(self.manager.get("/v/"))
        self.closed(self.manager.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}))

    def test_vendor_screen_ignores_login(self):
        c = self.entered()
        c.force_login(self.u4b)
        page = c.get("/v/")
        self.assertNotContains(page, self.m4b.name)
        self.assertNotContains(page, "로그아웃")
        c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": ""})
        # 요청자 본인 브라우저여도 업체로 바꾼 것이라 요청자에게 🔔이 간다(actor=None)
        self.assertTrue(Notification.objects.in_space(self.hub).filter(member=self.m4b, kind=Notification.Kind.REPAIR_UP).exists())


class ManageTests(VendorBase):
    def test_plain_member_forbidden_everywhere(self):
        for send, url in ((self.teacher.post, self.vurl), (self.teacher.post, f"{self.vurl}/revoke"),
                          (self.teacher.get, f"{self.vurl}/log"), (self.teacher.get, f"{self.vurl}/preview")):
            self.assertEqual(send(url).status_code, 403, url)
        self.assertFalse(VendorAccount.objects.exists())
        self.assertNotContains(self.teacher.get(self.base), "정보 업체 계정")

    def test_can_repair_member_can_issue(self):
        self.set_handler(self.m4b)
        self.assertEqual(self.issue(self.teacher).status_code, 200)
        self.assertEqual(VendorAccount.objects.in_space(self.hub).get().invited_by_id, self.m4b.pk)

    def test_outside_member_404(self):
        c = self.as_(self.far_user)
        self.assertEqual(c.post(self.vurl).status_code, 404)
        self.assertEqual(c.get(f"{self.vurl}/log").status_code, 404)

    def test_grade_space_404(self):
        res = self.as_(self.far_user).post(f"/s/{self.far.token}/repairs/vendor")
        self.assertEqual(res.status_code, 404)

    def test_form_errors(self):
        for bad in ({"name": ""}, {"contact_name": ""}, {"name": "가" * 101}, {"contact_name": "가" * 41}):
            self.assertEqual(self.issue(**bad).status_code, 400, bad)
        self.assertFalse(VendorAccount.objects.exists())

    def test_form_error_keeps_old_link_alive(self):
        _, link = self.issued()
        self.assertEqual(self.issue(name="").status_code, 400)
        self.assertEqual(Client().get(link).status_code, 302)

    def test_expired_plan_blocks_issue_but_not_revoke_or_log(self):
        self.issued()
        self.school.paid_until = timezone.localdate() - timedelta(days=1)
        self.school.save()
        self.assertEqual(self.issue().status_code, 403)
        self.assertEqual(self.manager.get(f"{self.vurl}/log").status_code, 200)
        self.assertEqual(self.manager.post(f"{self.vurl}/revoke").status_code, 200)
        self.assertFalse(VendorAccount.objects.filter(revoked_at__isnull=True).exists())

    def test_preview_read_only_and_not_logged(self):
        account, _ = self.issued()
        res = self.manager.get(f"{self.vurl}/preview")
        self.assertContains(res, MARK_TITLE)
        self.assertNotContains(res, MARK_NAME)
        self.assertNotContains(res, MARK_FACILITY)
        self.assertNotContains(res, "/state")
        self.assertFalse(VendorAccess.objects.filter(account=account).exists())

    def test_preview_uses_member_photo_url(self):
        self.issued()
        self.request(self.teacher, cat="info", title="사진요청", photo=up(jpeg_bytes()))
        r = Repair.objects.in_space(self.hub).get(title="사진요청")
        self.assertContains(self.manager.get(f"{self.vurl}/preview"), f"{self.base}/{r.key}/photo")

    def test_log_page_lists_only_this_school_and_only_30_days(self):
        _, link = self.issued()
        self.entered(link).get("/v/")
        far_hub = open_school(self.far_school, LATER, Member.objects.get(space__school=self.far_school, user=self.far_user))
        far = VendorAccount.objects.create(space=far_hub, name="먼업체", contact_name="먼담당자", token_hash="a" * 64, cookie_hash="b" * 64)
        VendorAccess.objects.create(account=far, action="view")
        old = VendorAccess.objects.create(account=VendorAccount.objects.in_space(self.hub).get(), action="state")
        VendorAccess.objects.filter(pk=old.pk).update(at=timezone.now() - timedelta(days=31))
        page = self.manager.get(f"{self.vurl}/log")
        self.assertNotContains(page, "상태·답변 바꿈")
        self.assertContains(page, "강업체")
        self.assertContains(page, "링크로 들어옴")
        self.assertNotContains(page, "먼담당자")

    def test_card_shows_account_and_copy_button_to_manager_only(self):
        account, link = self.issued()
        page = self.manager.get(self.it)
        self.assertContains(page, "정보 업체 계정")
        self.assertContains(page, "한빛정보")
        self.assertContains(page, "강업체")
        self.assertContains(page, "data-link-copy")
        self.assertNotContains(page, link)
        self.assertContains(page, "업체가 바뀌면 접근 끊기 뒤 새로 발급하세요(새 링크 발급은 담당자 교체용으로, 지금까지의 요청을 이어서 봐요).")
        for c, url in ((self.teacher, self.it), (self.teacher, self.base), (self.manager, self.base)):
            self.assertNotContains(c.get(url), "data-link-copy")

    def test_issue_response_updates_whole_list(self):
        """발급 직후 '누가 받나요'·부제목도 바뀐다(목록 화면 전체를 돌려주고 htmx가 #main을 바꾼다)."""
        res = self.issue()
        self.assertContains(res, "data-link-copy")
        self.assertNotContains(res, "/v/")
        self.assertContains(res, 'id="main"')
        self.assertContains(res, "계약 업체가 받아요")
        self.assertContains(res, "한빛정보 (계약 업체)")
        res = self.manager.post(f"{self.vurl}/revoke")
        self.assertNotContains(res, "계약 업체가 받아요")
        self.assertNotContains(res, "한빛정보 (계약 업체)")

    def test_forbidden_is_same_as_other_manager_screens(self):
        """일반 회원의 기록 화면은 관리자 전용 화면과 같은 빈 403."""
        a = self.teacher.get(f"{self.vurl}/log")
        b = self.teacher.get(f"/s/{self.hub.token}/school/attendance/clear")
        self.assertEqual(a.status_code, 403)
        self.assertEqual(a.content, b"")
        self.assertIn(b.status_code, (403, 405))

    def test_issue_and_revoke_logged_and_rung_to_all_managers(self):
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        self.set_handler(self.m4b)
        self.issue(self.teacher)
        self.issue(self.teacher, contact_name="새담당")
        self.teacher.post(f"{self.vurl}/revoke")
        logs = MemberLog.objects.in_space(self.hub).filter(name="한빛정보").order_by("pk")
        self.assertEqual([(x.by, x.before, x.after) for x in logs],
                         [("정하은", "없음", "링크 발급"), ("정하은", "링크 있음", "새 링크 발급"), ("정하은", "링크 있음", "접근 끊음")])
        bells = Notification.objects.in_space(self.hub).filter(kind=Notification.Kind.VENDOR)
        self.assertEqual(bells.filter(member=self.m4).count(), 3)
        self.assertEqual(bells.filter(member=self.m5).count(), 3)
        self.assertFalse(bells.filter(member=self.m4b).exists())
        self.assertFalse(bells.filter(pushed=False).exists())

    def test_reissue_hint_when_issuer_no_longer_handler(self):
        self.set_handler(self.m4b)
        self.issue(self.teacher)
        hint = "링크를 아는 선생님이 담당에서 빠졌어요. 새 링크를 발급하길 권해요."
        self.assertNotContains(self.manager.get(self.it), hint)
        Member.objects.filter(pk=self.m4b.pk).update(can_repair=False)
        self.assertContains(self.manager.get(self.it), hint)
        Member.objects.filter(pk=self.m5.pk).update(can_repair=True)
        self.assertContains(self.as_(self.u5).get(self.it), hint)  # 재발급할 수 있는 담당자에게도


class RequesterSideTests(VendorBase):
    def test_form_warning_names_vendor(self):
        self.assertNotContains(self.teacher.get(self.it), "외부 업체")
        self.issued()
        page = self.teacher.get(self.it)
        self.assertContains(page, "외부 업체(한빛정보)가 볼 수 있어요")
        self.assertContains(page, "학생 이름·개인정보는 적지 마세요")

    def test_list_and_detail_show_vendor_as_info_desk(self):
        self.issued()
        page = self.teacher.get(self.it)
        self.assertContains(page, "한빛정보 (계약 업체)")
        self.assertNotContains(self.teacher.get(self.base), "한빛정보")
        self.assertContains(self.teacher.get(f"{self.it}/{self.info.key}"), "한빛정보 (계약 업체)")
        self.assertNotContains(self.teacher.get(f"{self.base}/{self.facility.key}"), "한빛정보")

    def test_vendor_gone_returns_to_teacher_desk(self):
        account, _ = self.issued()
        VendorAccount.objects.filter(pk=account.pk).update(revoked_at=timezone.now())
        page = self.teacher.get(self.it)
        self.assertNotContains(page, "한빛정보")
        self.assertNotContains(page, "외부 업체")

    def test_plan_expired_vendor_not_shown_as_desk(self):
        self.issued()
        self.school.paid_until = timezone.localdate() - timedelta(days=1)
        self.school.save()
        self.assertNotContains(self.teacher.get(self.base), "한빛정보 (계약 업체)")
        self.assertNotContains(self.teacher.get(f"{self.base}/{self.info.key}"), "한빛정보")

    def test_reply_hint_and_vendor_reply_mark(self):
        self.set_handler(self.m5)
        staff = self.as_(self.u5)
        hint = "요청한 선생님과 업체가 봐요"
        self.assertNotContains(staff.get(f"{self.base}/{self.info.key}"), hint)
        _, link = self.issued()
        self.assertContains(staff.get(f"{self.base}/{self.info.key}"), hint)
        self.assertNotContains(staff.get(f"{self.base}/{self.facility.key}"), hint)
        c = self.entered(link)
        c.post(f"/v/r/{self.info.key}/state", {"status": "received", "reply": "내일 갑니다"})
        self.assertContains(staff.get(f"{self.base}/{self.info.key}"), "업체 답변")
        self.assertFalse(Notification.objects.filter(member=self.m4b, kind=Notification.Kind.REPAIR_UP).exists())  # 답변만은 🔔 없음
        staff.post(f"{self.base}/{self.info.key}/state", {"status": "received", "reply": "제가 볼게요"})
        self.assertNotContains(staff.get(f"{self.base}/{self.info.key}"), "업체 답변")

    def test_plain_member_never_sees_contact_name(self):
        self.issued()
        self.assertNotContains(self.teacher.get(self.base), "강업체")


class RetentionTests(VendorBase):
    def test_access_older_than_30_days_purged(self):
        account, _ = self.issued()
        old = VendorAccess.objects.create(account=account, action="view")
        new = VendorAccess.objects.create(account=account, action="view")
        VendorAccess.objects.filter(pk=old.pk).update(at=timezone.now() - timedelta(days=31))
        n = purge(timezone.now(), dry_run=True)
        self.assertEqual(n["vendor_access"], 1)
        self.assertTrue(VendorAccess.objects.filter(pk=old.pk).exists())
        purge(timezone.now())
        self.assertFalse(VendorAccess.objects.filter(pk=old.pk).exists())
        self.assertTrue(VendorAccess.objects.filter(pk=new.pk).exists())

    def test_gone_accounts_purged_after_30_days(self):
        gone, _ = self.issued()
        recent, _ = self.issued()
        live, _ = self.issued()
        VendorAccount.objects.filter(pk=gone.pk).update(revoked_at=timezone.now() - timedelta(days=31))
        VendorAccount.objects.filter(pk=live.pk).update(created_at=timezone.now() - timedelta(days=900))  # 오래됐어도 해제 전이면 그대로
        self.assertEqual(purge(timezone.now(), dry_run=True)["vendor_accounts"], 1)
        purge(timezone.now())
        self.assertCountEqual(VendorAccount.objects.values_list("pk", flat=True), [recent.pk, live.pk])

    def test_member_log_purged_after_a_year(self):
        old = MemberLog.objects.create(space=self.hub, by="a", name="b", before="c", after="d")
        new = MemberLog.objects.create(space=self.hub, by="a", name="b", before="c", after="d")
        MemberLog.objects.filter(pk=old.pk).update(at=timezone.now() - timedelta(days=366))
        self.assertEqual(purge(timezone.now(), dry_run=True)["member_logs"], 1)
        purge(timezone.now())
        self.assertEqual(list(MemberLog.objects.filter(pk__in=[old.pk, new.pk]).values_list("pk", flat=True)), [new.pk])

    def test_purge_command_prints_counts(self):
        out = StringIO()
        call_command("purge", "--dry-run", stdout=out)
        self.assertIn("업체 접속 기록 0건", out.getvalue())


class TitleMaskTests(VendorBase):
    """O1 후속: 이름 가림은 메모·답변뿐 아니라 제목·위치까지. 원문은 교내에 그대로."""

    def setUp(self):
        super().setUp()
        Repair.objects.filter(pk=self.info.pk).update(title=f"{self.m5.name} 선생님 전자칠판", place=f"{MARK_NAME} 자리 옆")
        self.c = self.entered()

    def test_vendor_list_and_preview_hide_names(self):
        for page in (self.c.get("/v/"), self.manager.get(f"{self.vurl}/preview")):
            body = page.content.decode()
            self.assertIn("○○○ 선생님 전자칠판", body)
            self.assertIn("○○○ 자리 옆", body)
            self.assertNotIn(f"{self.m5.name} 선생님 전자칠판", body)
            self.assertNotIn(MARK_NAME, body)

    def test_state_response_hides_names_and_keeps_original(self):
        res = self.c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": ""}, headers={"HX-Request": "true"})
        self.assertContains(res, "○○○ 선생님 전자칠판")
        self.assertNotContains(res, MARK_NAME)
        self.info.refresh_from_db()
        self.assertEqual(self.info.title, f"{self.m5.name} 선생님 전자칠판")
        self.assertEqual(self.info.place, f"{MARK_NAME} 자리 옆")
