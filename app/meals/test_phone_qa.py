"""M3 독립 검증: test_phone.py에 없는 경계. 실제 나가기·내보내기·지정 해제 경로, 실제 한도 값, Origin, 쓰기 쿼리, 닫힌 응답의 머리까지 같은지, 폴링 비용."""
import re
from unittest import mock

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from repairs.tests import jpeg_bytes, up
from spaces.models import Member

from . import phone
from .models import MAX_UPLOADS, MealPhoto, MealSettings, PhotoLink
from .test_phone import TOKEN, PhoneBase
from .tests import TUE

WRITES = ("INSERT", "UPDATE", "DELETE")


def writes(ctx):
    return [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith(WRITES)]


class RealPathsCloseTheLink(PhoneBase):
    """Member.update로 흉내 내지 않고 사용자가 실제로 누르는 경로로 권한을 뺀다."""

    def closed(self, raw):
        self.assertEqual(self.anon.get(f"/m/{raw}").status_code, 404)
        self.assertEqual(self.send(raw).status_code, 404)
        self.assertFalse(MealPhoto.objects.exists())

    def test_manager_unassigns_writer(self):
        raw = self.token()
        r = self.manager.post(f"/s/{self.hub.token}/school/members/{self.m5.key}/meal", {"on": "0"})
        self.assertNotEqual(r.status_code, 404)
        self.closed(raw)

    def test_writer_leaves_school(self):
        raw = self.token()
        r = self.writer.post(f"/s/{self.hub.token}/settings/leave", {"confirm": self.hub.name})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Member.objects.filter(pk=self.m5.pk).exists())
        self.closed(raw)
        self.assertFalse(PhotoLink.objects.exists())

    def test_manager_removes_member(self):
        raw = self.token()
        self.assertEqual(self.manager.post(f"/s/{self.hub.token}/members/{self.m5.key}/out").status_code, 302)
        self.closed(raw)

    def test_manager_link_dies_when_demoted(self):
        raw = self.token(self.manager)
        Member.objects.filter(pk=self.m4.pk).update(is_manager=False)
        self.closed(raw)


class ClosedLooksUnknown(PhoneBase):
    """몸만이 아니라 상태·쿠키·머리 이름까지 없는 토큰과 같아야 한다."""

    def shape(self, r):
        keep = sorted(k for k in r.headers if k.lower() != "content-length")
        return r.status_code, r.content, keep, sorted(r.cookies.keys())

    def test_every_closed_reason_matches_unknown(self):
        unknown_get = Client().get("/m/" + "x" * 43)
        unknown_post = Client().post("/m/" + "x" * 43, {"photo": up(jpeg_bytes())})
        cases = {
            "만료": lambda: PhotoLink.objects.update(expires_at="2020-01-01T00:00:00Z"),
            "횟수": lambda: PhotoLink.objects.update(uses=MAX_UPLOADS),
            "권한": lambda: Member.objects.filter(pk=self.m5.pk).update(can_meal=False),
        }
        for name, close in cases.items():
            with self.subTest(name):
                Member.objects.filter(pk=self.m5.pk).update(can_meal=True)
                raw = self.token()
                close()
                self.assertEqual(self.shape(Client().get(f"/m/{raw}")), self.shape(unknown_get))
                self.assertEqual(self.shape(Client().post(f"/m/{raw}", {"photo": up(jpeg_bytes())})), self.shape(unknown_post))


class GetWritesNothing(PhoneBase):
    def test_no_write_query_anon_or_logged_in(self):
        raw = self.token()
        for who, c in (("anon", Client()), ("writer", self.writer)):
            with self.subTest(who), CaptureQueriesContext(connection) as ctx:
                self.assertEqual(c.get(f"/m/{raw}").status_code, 200)
            self.assertEqual(writes(ctx), [])


class OriginTests(PhoneBase):
    def post_with(self, raw, **headers):
        c = Client(enforce_csrf_checks=True)
        html = c.get(f"/m/{raw}").content.decode()
        value = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        return c.post(f"/m/{raw}", {"photo": up(jpeg_bytes()), "csrfmiddlewaretoken": value}, **headers)

    def test_origin_null_is_refused_so_referrer_policy_matters(self):
        self.assertEqual(self.post_with(self.token(), HTTP_ORIGIN="null").status_code, 403)
        self.assertFalse(MealPhoto.objects.exists())

    def test_same_origin_passes(self):
        self.assertEqual(self.post_with(self.token(), HTTP_ORIGIN="http://testserver").status_code, 200)

    def test_policy_never_empties_origin(self):
        r = self.anon.get(f"/m/{self.token()}")
        self.assertNotIn(r["Referrer-Policy"], ("no-referrer", "same-origin; no-referrer"))


class CountsOnlySaved(PhoneBase):
    def test_lost_race_does_not_use_up_the_token(self):
        raw = self.token()
        with mock.patch("meals.phone.store_photo", return_value=False):
            self.assertEqual(self.send(raw).status_code, 400)
        self.assertEqual(PhotoLink.objects.get().uses, 0)


class RealLimits(PhoneBase):
    """test_phone은 한도를 mock으로 줄여 본다. 실제 값이 문서·기대와 같은지 값 그대로 돌린다."""

    def test_twenty_failed_tries_then_429_real_value(self):
        self.assertEqual(phone.TRIES_PER_HOUR, 20)
        raw = self.token()
        with mock.patch("meals.phone.UPLOADS_PER_IP", 10_000), mock.patch("meals.phone.HITS_PER_MINUTE", 10_000):
            for _ in range(20):
                self.assertEqual(self.send(raw, b"nope").status_code, 400)
            r = self.send(raw)
        self.assertEqual(r.status_code, 429)
        self.assertFalse(MealPhoto.objects.exists())
        self.assertEqual(PhotoLink.objects.get().uses, 0)


class TokenHolderLimits(PhoneBase):
    def test_token_cannot_touch_other_days_or_delete(self):
        other = MealPhoto.objects.create(space=self.hub, date=TUE.replace(day=2), stored=0)
        raw = self.token()
        self.send(raw)
        today = MealPhoto.objects.get(date=TUE)
        name = today.photo.name
        for data in ({"delete": "1"}, {"date": "2026-11-02"}, {"ymd": "20261102"}):
            self.assertEqual(self.anon.post(f"/m/{raw}", data).status_code, 400)
        self.assertEqual(MealPhoto.objects.get(date=TUE).photo.name, name)
        self.assertEqual(MealPhoto.objects.get(pk=other.pk).photo.name, "")

    def test_token_holder_gets_no_session_on_member_pages(self):
        raw = self.token()
        self.anon.get(f"/m/{raw}")
        self.send(raw)
        for path in (self.base, f"{self.base}/live", f"{self.base}/20261103/photo", f"/s/{self.hub.token}/"):
            self.assertIn(self.anon.get(path).status_code, (302, 403, 404), path)
        for path in (f"{self.base}/20261103/delete", f"{self.base}/20261104/upload"):
            self.assertIn(self.anon.post(path, {"photo": up(jpeg_bytes())}).status_code, (302, 403, 404), path)
        self.assertTrue(MealPhoto.objects.filter(date=TUE).exists())
        self.assertEqual(MealPhoto.objects.count(), 1)

    def test_phone_photo_reaches_tv_without_maker_name(self):
        MealSettings.objects.get_or_create(space=self.hub)
        tv = MealSettings.objects.in_space(self.hub).get().tv_token
        self.send(self.token())
        html = self.anon.get(f"/tv/{tv}/data").content.decode()
        self.assertIn(f"/tv/{tv}/photo/20261103", html)
        self.assertNotIn(self.m5.name, html)


class PollCost(PhoneBase):
    def url(self):
        html = self.writer.get(self.base).content.decode()
        return re.search(r'hx-get="([^"]*/meals/live[^"]*)"', html).group(1).replace("&amp;", "&")

    def test_quiet_poll_is_empty_and_cheap(self):
        url = self.url()
        with CaptureQueriesContext(connection) as ctx:
            r = self.writer.get(url, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.content, b"")
        self.assertEqual(writes(ctx), [])
        self.assertLessEqual(len(ctx.captured_queries), 8, [q["sql"][:80] for q in ctx.captured_queries])

    def test_pc_upload_does_not_trigger_a_redraw_that_wipes_warnings(self):
        r = self.writer.post(f"{self.base}/20261102/upload", {"photo": up(jpeg_bytes())}, headers={"HX-Request": "true"})
        html = r.content.decode()
        self.assertIn("칸이 맞는지", html)
        url = re.search(r'hx-get="([^"]*/meals/live[^"]*)"', html).group(1).replace("&amp;", "&")
        self.assertEqual(self.writer.get(url, headers={"HX-Request": "true"}).status_code, 204)

    def test_broken_until_redraws_without_500(self):
        for bad in ("abc", "9" * 5000, "1e99", ""):
            r = self.writer.get(f"{self.base}/live", {"v": "", "until": bad}, headers={"HX-Request": "true"})
            self.assertEqual(r.status_code, 200, bad[:10])
            self.assertEqual(TOKEN.findall(r.content.decode()), [])  # 다시 그려도 QR은 누를 때만
