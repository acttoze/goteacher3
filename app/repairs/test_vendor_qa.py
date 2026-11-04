"""K2 독립 검증(gt-qa): test_vendor.py에 없는 경계만. 날짜 경계·끊긴 응답 구별·토큰 바꿔 끼우기·DB 전 칼럼·보관 경계·동시 발급."""
import re
import threading
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.apps import apps
from django.core.cache import caches
from django.db import connection, connections
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import DEVICE_COOKIE, hash_token
from hwpfiles.retention import purge
from notices.models import Notice
from spaces.merge import open_school
from spaces.models import Member, School, Space

from .models import Repair, VendorAccess, VendorAccount
from .test_vendor import LATER, MARK_NAME, MARK_TITLE, VendorBase
from .tests import jpeg_bytes, up
from .vendor import COOKIE

KST = ZoneInfo("Asia/Seoul")


def kst(y, m, d, H=0, M=0, S=0):
    return datetime(y, m, d, H, M, S, tzinfo=KST)


def at(moment):
    """화면·권한이 보는 '지금'을 옮긴다. localdate도 이 값을 따른다."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


def scrub(res):
    """바이트 비교용: 요청마다 달라지는 값이 없어야 하므로 본문과 헤더 전부. 시계로 찍히는 Expires(never_cache)만 뺀다."""
    headers = sorted((k, v) for k, v in res.headers.items() if k != "Expires")
    return res.status_code, headers, res.content


class DateBoundaryTests(VendorBase):
    """사용 기간·플랜은 한국 시각 날짜로 끝난다(UTC로 계산하면 9시간 어긋난다)."""

    def setUp(self):
        super().setUp()
        self.account, self.link = self.issued()
        VendorAccount.objects.filter(pk=self.account.pk).update(until="2027-03-31")
        School.objects.filter(pk=self.school.pk).update(paid_until="2027-12-31")

    def test_until_last_second_open_next_second_closed(self):
        c = Client()
        with at(kst(2027, 3, 31, 23, 59, 59)):
            self.assertEqual(c.get(self.link).status_code, 302)
            self.assertContains(c.get("/v/"), MARK_TITLE)
        with at(kst(2027, 4, 1, 0, 0, 0)):
            self.closed(c.get("/v/"))
            self.closed(Client().get(self.link))
            self.closed(c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}))
            self.closed(c.get(f"/v/r/{self.info.key}/photo"))

    def test_utc_afternoon_is_already_next_day_in_korea(self):
        c = Client()
        with at(kst(2027, 3, 31, 12)):
            self.assertEqual(c.get(self.link).status_code, 302)
        utc_same_day = datetime(2027, 3, 31, 15, 0, 1, tzinfo=ZoneInfo("UTC"))  # KST 4/1 00:00:01
        with at(utc_same_day):
            self.closed(c.get("/v/"))

    def test_cookie_lives_only_until_midnight_after_last_day(self):
        with at(kst(2027, 3, 31, 23, 0)):
            res = Client().get(self.link)
        self.assertEqual(int(res.cookies[COOKIE]["max-age"]), 3600)

    def test_plan_last_day_open_next_day_closed(self):
        School.objects.filter(pk=self.school.pk).update(paid_until="2027-03-15")
        c = Client()
        with at(kst(2027, 3, 15, 23, 59, 59)):
            self.assertEqual(c.get(self.link).status_code, 302)
            self.assertContains(c.get("/v/"), MARK_TITLE)
        with at(kst(2027, 3, 16)):
            self.closed(c.get("/v/"))
            self.closed(Client().get(self.link))

    def test_issue_until_limits(self):
        with at(kst(2026, 11, 3, 10)):
            ok = self.issue(until="2026-11-03")
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(self.issue(until="2026-11-02").status_code, 400)
            self.assertEqual(self.issue(until="2027-11-04").status_code, 200)  # 오늘 + 366일
            self.assertEqual(self.issue(until="2027-11-05").status_code, 400)


class IndistinguishableTests(VendorBase):
    """없는 토큰과 끊긴 토큰(재발급·해제·기간·플랜·무료)은 상태·헤더·본문이 바이트까지 같다."""

    def unknown(self):
        return scrub(Client().get("/v/" + "x" * 43))

    def assert_same(self, link, c):
        want = self.unknown()
        self.assertEqual(scrub(Client().get(link)), want)
        self.assertEqual(scrub(c.get("/v/")), scrub(Client().get("/v/")))

    def test_reissued(self):
        _, link = self.issued()
        c = self.entered(link)
        self.issued()
        self.assert_same(link, c)

    def test_revoked(self):
        _, link = self.issued()
        c = self.entered(link)
        self.manager.post(f"{self.vurl}/revoke")
        self.assert_same(link, c)

    def test_clock_tick_between_requests_does_not_matter(self):
        _, link = self.issued()
        c = self.entered(link)
        self.issued()
        with mock.patch("django.utils.cache.http_date", side_effect=lambda _t, n=iter(range(99)): f"tick {next(n)}"):
            self.assert_same(link, c)

    def test_until_passed(self):
        account, link = self.issued()
        c = self.entered(link)
        VendorAccount.objects.filter(pk=account.pk).update(until=timezone.localdate() - timedelta(days=1))
        self.assert_same(link, c)

    def test_plan_expired_and_free(self):
        _, link = self.issued()
        c = self.entered(link)
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assert_same(link, c)
        School.objects.filter(pk=self.school.pk).update(paid_until=None, plan=School.Plan.FREE)
        self.assert_same(link, c)

    def test_closed_response_sets_no_cookie_and_names_no_school(self):
        _, link = self.issued()
        self.issued()
        res = Client().get(link)
        self.assertEqual(len(res.cookies), 0)
        self.assertNotContains(res, self.school.name, status_code=404)
        self.assertNotContains(res, "한빛정보", status_code=404)


class SwapTests(VendorBase):
    """토큰을 바꿔 끼우기: 다른 비밀 값으로 업체 쪽에, 업체 값으로 교내·기기 쪽에."""

    def setUp(self):
        super().setUp()
        self.account, self.link = self.issued()
        self.raw = self.link.split("/")[-1]
        self.c = self.entered(self.link)
        self.cookie = self.c.cookies[COOKIE].value
        self.account.refresh_from_db()
        self.notice = Notice.objects.create(space=self.hub, by=self.m4, by_name=self.m4.name, title="공지")

    def test_other_secrets_as_link(self):
        for value in (self.hub.token, self.hub.invite_token, self.c4.link_token, self.notice.key, self.info.key, self.cookie,
                      self.account.token_hash, self.account.cookie_hash, "r", self.raw[:-1], self.raw + "A", self.raw.upper()):
            self.closed(Client().get(f"/v/{value}"))

    def test_other_values_as_cookie(self):
        for value in (self.raw, self.account.token_hash, self.account.cookie_hash, hash_token(self.raw), self.hub.token,
                      self.cookie[:-1], self.cookie + "0", "", "x" * 129):
            c = Client()
            c.cookies[COOKIE] = value
            self.closed(c.get("/v/"))
            self.assertEqual(c.get(f"/v/r/{self.info.key}/photo").status_code, 404)
            self.assertEqual(c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}).status_code, 404)
        self.info.refresh_from_db()
        self.assertEqual(self.info.status, Repair.Status.RECEIVED)

    def test_vendor_token_is_not_a_space_or_collect_link(self):
        for path in (f"/s/{self.raw}/", f"/s/{self.raw}/repairs", f"/s/{self.cookie}/repairs", f"/c/{self.raw}", f"/n/{self.raw}", f"/j/{self.raw}"):
            for client in (Client(), self.c):
                res = client.get(path)
                self.assertIn(res.status_code, (302, 404), path)
                self.assertNotIn(MARK_TITLE, res.content.decode(errors="ignore"), path)

    def test_vendor_cookie_under_login_cookie_names_is_nothing(self):
        for name in ("sessionid", DEVICE_COOKIE):
            c = Client()
            c.cookies[name] = self.cookie
            res = c.get(f"{self.base}")
            self.assertEqual(res.status_code, 302, name)
            self.assertTrue(res["Location"].startswith("/start"), name)
            self.closed(c.get("/v/"))

    def test_cookie_of_other_school_sees_only_its_own(self):
        far_hub = open_school(self.far_school, LATER, Member.objects.get(space__school=self.far_school, user=self.far_user))
        far_member = Member.objects.in_space(far_hub).get(user=self.far_user)
        _, token = VendorAccount.issue(far_hub, "먼업체", "먼담당", LATER, far_member)
        Repair.objects.create(space=far_hub, by=far_member, by_name="먼사람", cat="info", title="먼학교요청")
        far = Client()
        far.get(f"/v/{token}")
        page = far.get("/v/")
        self.assertContains(page, "먼학교요청")
        self.assertNotContains(page, MARK_TITLE)
        self.assertEqual(far.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}).status_code, 404)
        self.assertEqual(far.get(f"/v/r/{self.info.key}/photo").status_code, 404)

    def test_recategorized_to_facility_disappears_for_vendor(self):
        self.assertContains(self.c.get("/v/"), MARK_TITLE)
        Repair.objects.filter(pk=self.info.pk).update(cat="facility")
        self.assertNotContains(self.c.get("/v/"), MARK_TITLE)
        self.assertEqual(self.c.post(f"/v/r/{self.info.key}/state", {"status": "done", "reply": ""}).status_code, 404)

    def test_junk_keys_are_404_not_500(self):
        for key in ("%00", "a%00b", "x" * 5000, "%ED%95%9C", "..%2f..", "' or 1=1 --"):
            self.assertEqual(self.c.get(f"/v/r/{key}/photo").status_code, 404, key)
            self.assertEqual(self.c.post(f"/v/r/{key}/state", {"status": "done", "reply": ""}).status_code, 404, key)
        self.assertEqual(Client().get("/v/%00").status_code, 404)
        self.closed(Client().get("/v/" + "x" * 5000))
        self.assertEqual(self.c.get("/v/?page=%00").status_code, 200)
        self.assertEqual(self.c.get("/v/?page=99999").status_code, 200)

    def test_nul_in_inputs_does_not_break(self):
        res = self.c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": "가\x00나"})
        self.assertEqual(res.status_code, 303)
        self.info.refresh_from_db()
        self.assertEqual(self.info.reply, "가나")
        self.assertEqual(self.c.post(f"/v/r/{self.info.key}/state", {"status": "do\x00ing", "reply": ""}).status_code, 400)
        self.assertEqual(self.issue(name="한빛\x00정보").status_code, 200)
        self.assertEqual(self.issue(until="nope\x00").status_code, 400)


class SecretsAtRestTests(VendorBase):
    def test_token_and_cookie_in_no_column_of_any_table(self):
        _, link = self.issued()
        raw = link.split("/")[-1]
        c = self.entered(link)
        cookie = c.cookies[COOKIE].value
        c.get("/v/")
        c.post(f"/v/r/{self.info.key}/state", {"status": "doing", "reply": "답"})
        self.manager.get(self.base)
        self.manager.get(f"{self.vurl}/log")
        hits, scanned = [], 0
        with connection.cursor() as cur:
            for table in connection.introspection.table_names():
                cur.execute(f'SELECT * FROM "{table}"')  # noqa: S608
                names = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    scanned += 1
                    for name, value in zip(names, row, strict=True):
                        text = value.decode(errors="ignore") if isinstance(value, (bytes, memoryview)) else str(value)
                        if raw in text or cookie in text:
                            hits.append(f"{table}.{name}")
        self.assertGreater(scanned, 50)
        self.assertEqual(hits, [])


class PhotoHeaderTests(VendorBase):
    def test_photo_headers_carry_no_requester_or_space(self):
        self.request(self.teacher, cat="info", title="사진요청", photo=up(jpeg_bytes(), name=f"{MARK_NAME}.jpg"))
        r = Repair.objects.in_space(self.hub).get(title="사진요청")
        Repair.objects.filter(pk=r.pk).update(by_name=MARK_NAME)
        res = self.entered().get(f"/v/r/{r.key}/photo")
        self.assertEqual(res.status_code, 200)
        body = b"".join(res.streaming_content)
        joined = " ".join(f"{k}: {v}" for k, v in res.headers.items())
        for secret in (MARK_NAME, self.m4b.name, self.hub.token, f"repair/{self.hub.pk}"):
            self.assertNotIn(secret, joined)
        self.assertNotIn(MARK_NAME.encode(), body)
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", res["Cache-Control"])
        self.assertEqual(res["Referrer-Policy"], "no-referrer")


class RetentionBoundaryTests(VendorBase):
    def test_access_just_inside_30_days_stays(self):
        account, _ = self.issued()
        now = timezone.now()
        keep = VendorAccess.objects.create(account=account, action="view")
        drop = VendorAccess.objects.create(account=account, action="view")
        VendorAccess.objects.filter(pk=keep.pk).update(at=now - timedelta(days=30) + timedelta(minutes=1))
        VendorAccess.objects.filter(pk=drop.pk).update(at=now - timedelta(days=30) - timedelta(minutes=1))
        purge(now)
        self.assertEqual(list(VendorAccess.objects.values_list("pk", flat=True)), [keep.pk])

    def test_account_inside_30_days_stays_and_live_expired_goes_after(self):
        account, _ = self.issued()
        now = timezone.now()
        VendorAccount.objects.filter(pk=account.pk).update(revoked_at=now - timedelta(days=29))
        purge(now)
        self.assertTrue(VendorAccount.objects.filter(pk=account.pk).exists())
        VendorAccount.objects.filter(pk=account.pk).update(revoked_at=now - timedelta(days=31))
        purge(now)
        self.assertFalse(VendorAccount.objects.filter(pk=account.pk).exists())

    def test_purging_account_also_purges_its_accesses_and_keeps_requests(self):
        account, _ = self.issued()
        VendorAccess.objects.create(account=account, action="view")
        VendorAccount.objects.filter(pk=account.pk).update(revoked_at=timezone.now() - timedelta(days=31))
        purge(timezone.now())
        self.assertEqual(VendorAccess.objects.count(), 0)
        self.assertTrue(Repair.objects.filter(pk=self.info.pk).exists())


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentIssueTests(TransactionTestCase):
    """PostgreSQL에서만: 동시에 발급해도 살아 있는 계정은 하나, 오류 없음. 어느 쪽이 이기든 진 쪽 링크는 끊겨 있다."""

    def test_two_issues_at_once(self):
        caches["ratelimit"].clear()
        school = School.objects.create(neis_atpt="J10", neis_code="7530001", name="동시초", plan=School.Plan.SCHOOL, paid_until=LATER)
        hub = Space.objects.create(school=school, grade=0, name="동시초", kind=Space.Kind.SCHOOL)
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create(username="google:conc", first_name="관리")
        Member.objects.create(space=hub, user=user, name="관리", cls="교무실", is_manager=True)
        results, gate = [], threading.Barrier(6)

        def press():
            try:
                c = Client()
                c.force_login(user)
                gate.wait(5)
                res = c.post(f"/s/{hub.token}/repairs/vendor", {"name": "업체", "contact_name": "담당", "until": LATER.isoformat()})
                results.append((res.status_code, res.content.decode()))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=press) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual([r[0] for r in results], [200] * 6)
        self.assertEqual(VendorAccount.objects.filter(space=hub, revoked_at__isnull=True).count(), 1)
        links = [re.search(r"(/v/[\w-]{40,})", r[1]).group(1) for r in results]
        self.assertEqual(sorted(Client().get(link).status_code for link in links), [302] + [404] * 5)
        apps.get_model("spaces", "Space").objects.filter(pk=hub.pk).delete()
