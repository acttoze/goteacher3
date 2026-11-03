"""I2 QA(독립 검증): test_seen.py에 없는 경계만.

- 받는 사람 응답은 남이 보든 말든 한 글자도 달라지면 안 된다(이름 검색보다 강한 차분 비교).
- 로그인 뒤 복귀, CSRF, 손님·회원 나감·쓴 사람 나감, 충돌·오류 저장의 🔔, 🔔 뒤처리.
"""
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from collect.models import Notification
from spaces.models import Member, School, Space

from .models import Notice, NoticeSeen
from .test_seen import STATUS_MARKS, SeenBase

User = get_user_model()
NOISE = re.compile(r'name="csrfmiddlewaretoken" value="[^"]+"|csrf[^"\s]*"\s*:\s*"[^"]+"|data-csrf="[^"]+"')


class QaBase(SeenBase):
    def setUp(self):
        super().setUp()
        for name in ("schedule", "meals"):
            p = mock.patch(f"spaces.neis.{name}", return_value=[])
            p.start()
            self.addCleanup(p.stop)
        self.tok = self.hub.token

    def receiver_urls(self, n):
        return [f"{self.base}/{n.key}/", f"{self.base}/{n.key}/?v=1", self.base, f"{self.base}?page=1",
                f"/s/{self.tok}/today", f"/s/{self.tok}/today?show=school", f"/s/{self.tok}/today?show=me",
                f"/s/{self.tok}/bell", f"/s/{self.tok}/schedule", f"/n/{n.key}"]

    def snap(self, client, urls):
        out = {}
        for url in urls:
            r = client.get(url)
            out[url] = (r.status_code, NOISE.sub("", r.content.decode()))
        return out


class ReceiverLeakDiffTests(QaBase):
    def test_receiver_bytes_identical_whatever_others_did(self):
        """받는 사람(m5) 응답 원문 전체를, 남이 보기·확인·재확인한 전후로 비교한다."""
        m4 = self.as_("m4")
        plain = self.made(m4, title="일반 공지")
        must = self.made(m4, must="on", title="필수 공지")
        for n in (plain, must):
            self.edit(n, m4, 장소="체육관", real="on", must="on" if n is must else "")
        rec = self.as_("m5")
        urls = self.receiver_urls(plain) + self.receiver_urls(must)
        before = self.snap(rec, urls)
        for who in ("quiet", "m4b"):
            c = self.as_(who)
            self.seen(plain, c, v=1)
            self.seen(plain, c)
            self.seen(must, c)
            self.ack(must, c)
        self.assertEqual(NoticeSeen.objects.filter(acked=True).count(), 2)
        after = self.snap(rec, urls)
        for url in urls:
            self.assertEqual(before[url], after[url], url)

    def test_names_of_others_absent_from_receiver_pages(self):
        n = self.made(self.as_("m4"), must="on", title="필수 공지")
        self.ack(n, self.as_("quiet"))
        rec = self.as_("m5")
        for url in self.receiver_urls(n)[:8]:  # 일정·쪽지 링크 빼고: 현황이 실릴 수 있는 곳 전부
            body = rec.get(url).content.decode()
            for name in ("비밀선생", "정하은"):  # 대상이지만 쓴 사람이 아닌 동료
                self.assertNotIn(name, body, url)
            for mark in STATUS_MARKS:
                self.assertNotIn(mark, body, url)

    def test_non_manager_with_title_vice_principal_is_still_a_receiver(self):
        Member.objects.filter(pk=self.m5.pk).update(position="교감")
        n = self.made(self.as_("m4"))
        page = self.as_("m5").get(f"{self.base}/{n.key}/").content.decode()
        for mark in STATUS_MARKS:
            self.assertNotIn(mark, page)

    def test_status_gone_after_manager_rights_removed_and_seen_by_writer_only(self):
        n = self.made(self.client)
        self.assertContains(self.client.get(f"{self.base}/{n.key}/"), "확인 현황")
        self.assertNotContains(self.as_("m5").get(f"{self.base}/{n.key}/"), "확인 현황")
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        self.assertContains(self.as_("m5").get(f"{self.base}/{n.key}/"), "확인 현황")
        Member.objects.filter(pk=self.m5.pk).update(is_manager=False)
        self.assertNotContains(self.as_("m5").get(f"{self.base}/{n.key}/"), "확인 현황")


class RecordEdgeTests(QaBase):
    def test_csrf_enforced_on_seen_and_ack(self):
        n = self.made(self.as_("m4"), must="on")
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.u5)
        self.assertEqual(c.post(f"{self.base}/{n.key}/seen").status_code, 403)
        self.assertEqual(c.post(f"{self.base}/{n.key}/ack").status_code, 403)
        self.assertFalse(NoticeSeen.objects.exists())

    def test_head_and_bot_agents_never_record(self):
        n = self.made()
        for ua in ("Slackbot-LinkExpanding 1.0", "kakaotalk-scrap/1.0", "Mozilla/5.0"):
            c = Client(HTTP_USER_AGENT=ua)
            c.force_login(self.u5)
            c.head(f"/n/{n.key}")
            c.get(f"/n/{n.key}", follow=True)
            c.head(f"{self.base}/{n.key}/")
        self.assertFalse(NoticeSeen.objects.exists())

    def test_get_on_seen_and_ack_not_allowed(self):
        n = self.made(self.as_("m4"), must="on")
        m5 = self.as_("m5")
        self.assertEqual(m5.get(f"{self.base}/{n.key}/seen").status_code, 405)
        self.assertEqual(m5.get(f"{self.base}/{n.key}/ack").status_code, 405)
        self.assertFalse(NoticeSeen.objects.filter(member=self.m5).exists())

    def test_bad_versions(self):
        n = self.made()
        m5 = self.as_("m5")
        for bad, code in (("", 400), ("-1", 400), ("1.5", 400), ("٣", 400), ("99999999999999", 400), ("0", 404), ("2", 404)):
            self.assertEqual(self.seen(n, m5, v=bad).status_code, code, bad)
        self.assertFalse(NoticeSeen.objects.exists())

    def test_anonymous_ack_refused_and_deleted_notice_404(self):
        n = self.made(self.as_("m4"), must="on")
        self.assertEqual(self.ack(n, Client()).status_code, 302)
        self.assertFalse(NoticeSeen.objects.exists())
        m5 = self.as_("m5")
        self.as_("m4").post(f"{self.base}/{n.key}/delete")
        self.assertEqual(self.seen(n, m5).status_code, 404)
        self.assertEqual(self.ack(n, m5).status_code, 404)

    def test_old_version_post_does_not_read_change_bell(self):
        n = self.made()
        m5 = self.as_("m5")
        self.seen(n, m5)
        self.edit(n, 장소="체육관", real="on")
        self.seen(n, m5, v=1)
        self.assertFalse(self.bells(n, "change").get().read)

    def test_ack_on_later_version_after_seen_keeps_one_row_per_version(self):
        n = self.made(self.as_("m4"), must="on")
        m5 = self.as_("m5")
        self.seen(n, m5)
        self.ack(n, m5)
        self.ack(n, m5)
        self.assertEqual(NoticeSeen.objects.filter(member=self.m5).count(), 1)
        self.assertEqual(self.as_("m5").get(f"{self.base}/{n.key}/").status_code, 200)


class MustForgeryTests(QaBase):
    def test_writer_non_manager_cannot_flip_must_either_way(self):
        n = self.made(self.client)  # m4b 일반 회원이 씀
        self.assertEqual(self.edit(n, self.client, must="on").status_code, 403)
        self.edit(n, self.as_("m4"), must="on")
        n.refresh_from_db()
        self.assertTrue(n.must)
        self.edit(n, self.client)  # 값 없이 저장: 끄지 못한다
        n.refresh_from_db()
        self.assertTrue(n.must)
        self.assertEqual(self.edit(n, self.client, must="on").status_code, 302)  # 이미 켜진 값을 보내도 그대로
        n.refresh_from_db()
        self.assertTrue(n.must)

    def test_forged_must_refused_saves_nothing_even_with_title_position(self):
        for pos in ("교감", "교장", "부장", "관리자"):
            Member.objects.filter(pk=self.m4b.pk).update(position=pos)
            self.assertEqual(self.write(must="on", title=f"위조 {pos}").status_code, 403)
        self.assertFalse(Notice.objects.filter(title__startswith="위조").exists())
        self.assertFalse(Notification.objects.filter(kind="notice").exists())

    def test_expired_school_manager_cannot_turn_on(self):
        n = self.made(self.client)
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.edit(n, self.as_("m4"), must="on").status_code, 403)
        n.refresh_from_db()
        self.assertFalse(n.must)


class BellEdgeTests(QaBase):
    def test_conflict_and_invalid_edit_make_no_bell(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.assertEqual(self.edit(n, 장소="체육관", real="on", base_v=0).status_code, 409)
        self.assertEqual(self.edit(n, title="", real="on").status_code, 400)
        self.assertFalse(self.bells(n).exists())

    def test_must_and_real_edit_no_same_kind_duplicates(self):
        m4 = self.as_("m4")
        n = self.made(m4)
        self.seen(n, self.as_("m5"))
        self.edit(n, m4, 장소="체육관", real="on", must="on")
        # 리드 결정: 같은 저장의 확인 필수 + 실질 변경은 한 건으로 합친다
        bell = self.bells(n).get(member=self.m5)
        self.assertEqual(bell.kind, "notice")
        self.assertIn("장소 강당 → 체육관", bell.text)

    def test_resubmit_same_real_change_no_second_bell(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.edit(n, 장소="체육관", real="on")
        n.refresh_from_db()
        self.edit(n, real="on")  # 같은 값 다시 저장(새 판 없음)
        self.assertEqual(self.bells(n, "change").count(), 1)

    def test_already_acked_person_gets_no_new_must_bell(self):
        """관리자가 남의 공지에 확인 필수를 켜면 본인은 🔔 없음 → 눌러 확인 → 다른 사람이 고침. 이미 확인했으니 🔔이 새로 오면 안 된다."""
        m4 = self.as_("m4")
        n = self.made(self.client)
        self.edit(n, m4, must="on")
        self.ack(n, m4)
        n.refresh_from_db()
        self.edit(n, self.client, 준비물="실내화")  # 쓴 사람이 고침(실질 변경 아님)
        self.assertFalse(self.bells(n, "notice").filter(member=self.m4, read=False).exists())

    def test_narrowed_audience_drops_unread_must_bell(self):
        """대상에서 빠진 사람에게 남은 🔔을 누르면 404로 떨어지면 안 된다."""
        m4 = self.as_("m4")
        n = self.made(m4, must="on")
        self.edit(n, m4, must="on", aud="groups", groups=["g4"])
        m5 = self.as_("m5")
        left = self.bells(n, "notice").filter(member=self.m5, read=False).first()
        if left is not None:
            r = m5.post(f"/s/{self.tok}/bell/{left.key}", follow=True)
            self.assertEqual(r.status_code, 200)

    def test_must_turned_off_bell_stops_showing_as_todo(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on")
        self.edit(n, m4)
        rec = self.as_("m5")
        self.assertNotContains(rec.get(self.base), "아직 안 봄")
        self.assertNotContains(rec.get(f"/s/{self.tok}/today"), "아직 안 봄")
        self.assertEqual(self.ack(n, rec).status_code, 400)

    def test_bell_text_escaped_and_truncated(self):
        n = self.made(title="<script>x</script>")
        self.seen(n, self.as_("m5"))
        self.edit(n, 장소="가" * 200, 일시="나" * 200, 준비물="다" * 200, real="on")
        bell = self.bells(n, "change").get()
        self.assertLessEqual(len(bell.text), 300)
        panel = self.as_("m5").get(f"/s/{self.tok}/bell").content.decode()
        self.assertNotIn("<script>x", panel)
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertNotIn('data-text="[고티처] <script>', page)

    def test_quote_in_title_does_not_break_copy_attribute(self):
        n = self.made(title='"><img src=x onerror=alert(1)>')
        page = self.client.get(f"{self.base}/{n.key}/").content.decode()
        self.assertNotIn("<img src=x", page)


class CountConsistencyTests(QaBase):
    def status(self, n, client=None):
        return (client or self.as_("m4")).get(f"{self.base}/{n.key}/").context["status"]

    def test_removed_member_leaves_totals_consistent(self):
        n = self.made(self.client)
        self.seen(n, self.as_("m5"))
        self.seen(n, self.as_("quiet"))
        self.assertEqual((self.status(n)["seen"], self.status(n)["total"]), (3, 4))
        self.m5.delete()
        st = self.status(n)
        self.assertEqual((st["seen"], st["total"]), (2, 3))
        self.assertEqual([m["name"] for m in st["missing"]], [self.m4.name])

    def test_removed_writer_notice_still_works_for_everyone(self):
        n = self.made(self.client)
        self.seen(n, self.as_("m5"))
        self.m4b.delete()
        n.refresh_from_db()
        self.assertIsNone(n.by_id)
        st = self.status(n)
        self.assertEqual((st["seen"], st["total"]), (1, 3))
        self.assertEqual(self.edit(n, self.as_("m4"), 장소="체육관", real="on", must="on").status_code, 302)
        self.assertIn("체육관", self.bells(n).get(member=self.m5).text)  # 앞 판을 본 m5만, 리드 결정대로 한 건으로 합침
        self.assertNotIn("체육관", self.bells(n).get(member=self.quiet).text)
        self.assertEqual(set(self.bells(n, "notice").values_list("member_id", flat=True)), {self.m5.pk, self.quiet.pk})
        self.assertContains(self.as_("m5").get(self.base), "아직 안 봄")
        self.assertContains(self.as_("m5").get(f"/s/{self.tok}/today"), "아직 안 봄")

    def test_group_moves_and_audience_change_percent_never_over_100(self):
        n = self.made(self.client, aud="groups", groups=["g4"])
        self.seen(n, self.as_("quiet"))
        self.assertEqual((self.status(n)["seen"], self.status(n)["total"]), (2, 3))
        # 대상을 좁히고 넓혀도 기록은 남고 100%를 넘지 않는다
        self.edit(n, aud="groups", groups=["g5"])
        n.refresh_from_db()
        st = self.status(n)
        self.assertEqual((st["seen"], st["total"]), (0, 1))
        self.edit(n, aud="all")
        n.refresh_from_db()
        st = self.status(n)
        self.assertEqual((st["seen"], st["total"]), (2, 4))
        self.assertLessEqual(st["pct"], 100)

    def test_recheck_denominator_follows_audience_and_removal(self):
        n = self.made(self.client)
        self.seen(n, self.as_("m5"))
        self.seen(n, self.as_("quiet"))
        self.edit(n, 장소="체육관", real="on")
        n.refresh_from_db()
        self.assertEqual(self.status(n)["recheck"]["m"], 2)
        self.quiet.delete()
        self.assertEqual(self.status(n)["recheck"]["m"], 1)
        Member.objects.filter(pk=self.m5.pk).update(group="g6")
        self.assertEqual(self.status(n)["recheck"]["m"], 1)  # 대상 'all'이라 그대로

    def test_manager_outside_audience_does_not_inflate(self):
        n = self.made(self.as_("m5"), aud="groups", groups=["g5"])
        self.seen(n, self.as_("m4"))
        st = self.status(n)
        self.assertEqual((st["seen"], st["total"], st["pct"]), (1, 1, 100))
        self.assertEqual(st["missing"], [])

    def test_writer_outside_audience_not_counted(self):
        n = self.made(self.client, aud="groups", groups=["g5"])
        st = self.status(n, self.client)
        self.assertEqual((st["seen"], st["total"], [m["name"] for m in st["missing"]]), (0, 1, [self.m5.name]))

    def test_zero_audience_is_zero_not_error(self):
        n = self.made(self.as_("m4"), aud="groups", groups=["g6"])
        st = self.status(n)
        self.assertEqual((st["seen"], st["total"], st["pct"]), (0, 0, 0))
        self.assertEqual(self.as_("m4").get(self.base).status_code, 200)

    def test_guest_seat_absorbed_keeps_status(self):
        n = self.made(self.client)
        guest = User.objects.create(username="guest:i2", first_name="손님")
        Member.objects.create(space=self.hub, user=guest, name="손님", cls="4-5", group="g4")
        c = Client()
        c.force_login(guest)
        self.assertEqual(self.seen(n, c).status_code, 204)
        self.assertEqual(self.status(n)["seen"], 2)


class LinkReturnTests(QaBase):
    def test_login_round_trip_returns_to_notice(self):
        """익명 → /start → 구글 → 콜백 → /n/<토큰> → 상세. 구글 응답만 목."""
        n = self.made(title="비밀 제목")
        u = User.objects.create(username="google:i2round", first_name="왕복")
        Member.objects.create(space=self.hub, user=u, name="왕복", cls="4-9", group="g4")
        c = Client()
        start = c.get(f"/n/{n.key}")["Location"]
        page = c.get(start)
        self.assertNotContains(page, "비밀 제목")
        nxt = re.search(r'name="next" value="([^"]+)"', page.content.decode()).group(1)
        with override_settings(GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="s"):
            r = c.get("/auth/google/login", {"next": nxt})
            self.assertEqual(r.status_code, 302)
            state = c.session["google_state"]
            info = {"sub": "i2round", "email": "a@b.c", "email_verified": True, "name": "왕복"}
            with mock.patch("accounts.views._http_json", side_effect=[{"access_token": "t"}, info]):
                r = c.get("/auth/google/callback", {"state": state, "code": "x"})
        self.assertEqual(r["Location"], f"/n/{n.key}")
        r = c.get(r["Location"])
        self.assertRedirects(r, f"{self.base}/{n.key}/", fetch_redirect_response=False)
        self.assertContains(c.get(r["Location"]), "비밀 제목")

    def test_guest_member_opens_link(self):
        n = self.made(title="손님 공지")
        guest = User.objects.create(username="guest:i2g", first_name="손님")
        Member.objects.create(space=self.hub, user=guest, name="손님", cls="4-5", group="g4")
        c = Client()
        c.force_login(guest)
        self.assertRedirects(c.get(f"/n/{n.key}"), f"{self.base}/{n.key}/", fetch_redirect_response=False)

    def test_other_school_member_and_guest_404_same_body_as_missing(self):
        n = self.made(title="비밀 제목")
        other = School.objects.create(neis_atpt="B10", neis_code="1111111", name="다른초")
        far = Space.objects.create(school=other, grade=1, name="다른초 1학년")
        for username in ("google:i2x", "guest:i2x"):
            u = User.objects.create(username=username)
            Member.objects.create(space=far, user=u, name="남", cls="1-1")
            c = Client()
            c.force_login(u)
            a, b = c.get(f"/n/{n.key}"), c.get("/n/없는토큰")
            self.assertEqual((a.status_code, a.content), (b.status_code, b.content))
            self.assertEqual(a.status_code, 404)
            self.assertIn("noindex", a["X-Robots-Tag"])

    def test_anonymous_responses_identical_except_token(self):
        n = self.made(title="비밀 제목")
        a, b = Client().get(f"/n/{n.key}"), Client().get("/n/AAAAAAAA")
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(a.content, b.content)
        self.assertNotIn("비밀 제목", a.content.decode() + Client().get(a["Location"]).content.decode())
        self.assertNotIn("비밀 제목", a["Location"])

    def test_key_is_not_space_token_or_row_key(self):
        n = self.made()
        c = self.as_("m5")
        self.assertEqual(c.get(f"/n/{self.hub.token}").status_code, 404)
        self.assertEqual(c.get(f"/n/{self.m5.key}").status_code, 404)
        self.assertGreaterEqual(len(n.key), 16)

    def test_old_grade_space_url_seen_goes_to_hub_or_404_never_records_elsewhere(self):
        n = self.made()
        r = self.as_("m5").post(f"/s/{self.g5.token}/board/{n.key}/seen")
        self.assertIn(r.status_code, (301, 302, 303, 307, 308, 404))
        self.assertFalse(NoticeSeen.objects.exclude(notice__space=self.hub).exists())


class TokenSwapTests(QaBase):
    def test_other_school_notice_key_under_my_space_404(self):
        """학교 공간이 둘일 때 공지 key·공간 토큰을 바꿔 끼운다. 다른 학교 관리자도 마찬가지."""
        other = School.objects.create(neis_atpt="B10", neis_code="2222222", name="옆학교", plan="school",
                                      paid_until=timezone.localdate() + timedelta(days=30))
        far = Space.objects.create(school=other, grade=0, kind=Space.Kind.SCHOOL, name="옆학교")
        u = User.objects.create(username="google:i2swap", first_name="옆관리자")
        boss = Member.objects.create(space=far, user=u, name="옆관리자", cls="", group="admin", is_manager=True)
        fn = Notice.objects.create(space=far, title="옆학교 비밀", by=boss, by_name=boss.name, must=True)
        fn.versions.create(v=1, fields={"제목": fn.title}, by=boss.name)
        n = self.made(self.as_("m4"), must="on", title="우리 비밀")
        c = Client()
        c.force_login(u)
        m4 = self.as_("m4")
        for client, space_tok, key in ((m4, self.tok, fn.key), (c, far.token, n.key)):
            base = f"/s/{space_tok}/board/{key}"
            self.assertEqual(client.get(f"{base}/").status_code, 404)
            self.assertEqual(client.post(f"{base}/seen").status_code, 404)
            self.assertEqual(client.post(f"{base}/ack").status_code, 404)
        self.assertEqual(c.post(f"{self.base}/{n.key}/seen").status_code, 404)  # 남의 공간 주소 자체
        self.assertEqual(m4.get(f"/n/{fn.key}").status_code, 404)
        self.assertEqual(c.get(f"/n/{n.key}").status_code, 404)
        self.assertFalse(NoticeSeen.objects.exists())
        self.assertNotContains(c.get(f"/s/{far.token}/board"), "우리 비밀")
        self.assertNotContains(m4.get(self.base), "옆학교 비밀")
        self.assertNotContains(m4.get(f"/s/{self.tok}/today?show=school"), "옆학교 비밀")


class ExpiredTests(QaBase):
    def test_expired_all_read_paths_and_record_work_writes_403(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on", title="만료 공지")
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        m5 = self.as_("m5")
        for url in (f"/n/{n.key}", self.base, f"{self.base}/{n.key}/", f"/s/{self.tok}/today", f"/s/{self.tok}/bell"):
            self.assertIn(m5.get(url).status_code, (200, 302), url)
        self.assertEqual(self.seen(n, m5).status_code, 204)
        self.assertEqual(self.ack(n, m5).status_code, 303)
        self.assertContains(m4.get(f"{self.base}/{n.key}/"), "2/4명")
        self.assertEqual(self.write(m4, title="새 공지").status_code, 403)
        self.assertEqual(self.edit(n, m4, 장소="체육관", real="on").status_code, 403)
        self.assertEqual(m4.post(f"{self.base}/{n.key}/delete").status_code, 403)
        self.assertEqual(self.bells(n, "change").count(), 0)


class ScaleTests(QaBase):
    def test_real_change_to_200_seen_is_bulk_and_constant_queries(self):
        Member.objects.bulk_create([Member(space=self.hub, name=f"교사{i}", cls=f"1-{i}", group="g1") for i in range(200)])
        n = self.made(self.client)
        NoticeSeen.objects.bulk_create([NoticeSeen(notice=n, member=m, v=1) for m in Member.objects.in_space(self.hub)])
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(self.edit(n, 장소="체육관", real="on").status_code, 302)
        inserts = [x for x in q if x["sql"].startswith("INSERT") and "collect_notification" in x["sql"]]
        self.assertLess(len(inserts), 5)
        self.assertEqual(self.bells(n, "change").count(), 203)
        self.assertLess(len(q), 40)

    def test_detail_status_with_200_seen_constant(self):
        n = self.made(self.client)
        with CaptureQueriesContext(connection) as q1:
            self.client.get(f"{self.base}/{n.key}/")
        Member.objects.bulk_create([Member(space=self.hub, name=f"교사{i}", cls=f"1-{i}", group="g1") for i in range(200)])
        NoticeSeen.objects.bulk_create([NoticeSeen(notice=n, member=m, v=1) for m in Member.objects.in_space(self.hub)[:150]])
        with CaptureQueriesContext(connection) as q2:
            page = self.client.get(f"{self.base}/{n.key}/")
        self.assertEqual(len(q1), len(q2))
        self.assertLessEqual(page.context["status"]["pct"], 100)
