"""I2 확인·현황·실질 변경·🔔·쪽지 링크.

불변식
- 받는 사람의 어떤 응답에도 다른 사람의 확인 여부·비율이 없다. 현황은 쓴 사람과 관리자만.
- GET·봇으로는 봄 기록이 생기지 않는다. 익명·다른 공간·대상 밖은 기록 못 함.
- 확인 필수는 is_manager만 켠다(직책 글자 무관).
- 실질 변경은 이전 판을 본 사람에게만 🔔. 새 공지 🔔은 확인 필수만, 대상 전원.
- 쪽지 링크는 익명에게 내용을 보이지 않고 회원·대상만 상세로.
"""
import re
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from collect import push
from collect.models import Notification, PushSubscription
from spaces.merge import open_school
from spaces.models import Member, School, Space

from .models import Notice, NoticeSeen
from .tests import Base

STATUS_MARKS = ("확인 현황", "안 본 선생님", "안 본 분께", "바뀐 부분 확인", "열어 본 분")
RATIO = re.compile(r"\d+\s*/\s*\d+\s*명|확인\s*\d+\s*%")


class SeenBase(Base):
    def setUp(self):
        super().setUp()
        # 아무도 안 본 g4 동료 하나: 이 이름이 받는 사람 응답에 나오면 현황이 샌 것이다
        self.u_quiet = get_user_model().objects.create(username="google:q", first_name="비밀선생")
        self.quiet = Member.objects.create(space=self.hub, user=self.u_quiet, name="비밀선생", cls="4-3", group="g4")
        self.u["quiet"] = self.u_quiet

    def seen(self, n, client=None, **data):
        return (client or self.client).post(f"{self.base}/{n.key}/seen", data)

    def ack(self, n, client=None):
        return (client or self.client).post(f"{self.base}/{n.key}/ack")

    def bells(self, n, kind=None):
        q = Notification.objects.filter(notice=n)
        return q.filter(kind=kind) if kind else q

    def assert_no_status(self, resp):
        body = resp.content.decode()
        for mark in STATUS_MARKS:
            self.assertNotIn(mark, body)
        self.assertIsNone(RATIO.search(body), RATIO.search(body))
        self.assertNotIn("비밀선생", body)


class RecordTests(SeenBase):
    def test_get_never_records(self):
        n = self.made()
        for who in ("m4", "m5", "quiet"):
            self.assertEqual(self.as_(who).get(f"{self.base}/{n.key}/").status_code, 200)
        bot = Client(HTTP_USER_AGENT="facebookexternalhit/1.1")
        bot.get(f"/n/{n.key}")
        bot.get(f"{self.base}/{n.key}/")
        self.assertFalse(NoticeSeen.objects.exists())

    def test_anonymous_post_refused(self):
        n = self.made()
        r = self.seen(n, Client())
        self.assertEqual(r.status_code, 302)
        self.assertFalse(NoticeSeen.objects.exists())

    def test_post_records_latest_once(self):
        n = self.made()
        m5 = self.as_("m5")
        self.assertEqual(self.seen(n, m5).status_code, 204)
        self.assertEqual(self.seen(n, m5).status_code, 204)
        row = NoticeSeen.objects.get()
        self.assertEqual((row.notice, row.member, row.v, row.acked), (n, self.m5, 1, False))

    def test_post_with_version(self):
        n = self.made()
        self.edit(n, 장소="체육관")
        self.seen(n, self.as_("m5"), v=1)
        self.assertEqual(NoticeSeen.objects.get().v, 1)
        self.assertEqual(self.seen(n, self.as_("m5"), v=9).status_code, 404)
        self.assertEqual(self.seen(n, self.as_("m5"), v="x").status_code, 400)

    def test_outside_audience_and_other_space_404(self):
        n = self.made(aud="groups", groups=["g4"])
        self.assertEqual(self.seen(n, self.as_("m5")).status_code, 404)
        self.assertEqual(self.ack(n, self.as_("m5")).status_code, 404)
        other = School.objects.create(neis_atpt="B10", neis_code="1111111", name="다른초")
        far = Space.objects.create(school=other, grade=1, name="다른초 1학년")
        u = get_user_model().objects.create(username="google:i2far")
        Member.objects.create(space=far, user=u, name="남", cls="1-1")
        c = Client()
        c.force_login(u)
        self.assertEqual(self.seen(n, c).status_code, 404)  # 그 공간 회원이 아니다
        self.assertEqual(c.post(f"/s/{far.token}/board/{n.key}/seen").status_code, 404)  # 동학년 공간에는 없는 주소
        self.assertFalse(NoticeSeen.objects.exists())

    def test_detail_gives_form_only_on_latest_unseen(self):
        n = self.made()
        self.edit(n, 장소="체육관")
        m5 = self.as_("m5")
        self.assertContains(m5.get(f"{self.base}/{n.key}/"), 'id="seenForm"')
        self.assertNotContains(m5.get(f"{self.base}/{n.key}/?v=1"), 'id="seenForm"')
        self.seen(n, m5)
        self.assertNotContains(m5.get(f"{self.base}/{n.key}/"), 'id="seenForm"')
        self.assertNotContains(m5.get(f"{self.base}/{n.key}/"), "<script>")

    def test_expired_school_reads_and_records_but_cannot_write(self):
        n = self.made()
        m5 = self.as_("m5")
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(m5.get(f"{self.base}/{n.key}/").status_code, 200)
        self.assertEqual(self.seen(n, m5).status_code, 204)
        self.assertEqual(self.write().status_code, 403)
        self.assertEqual(self.edit(n, 장소="다른 곳").status_code, 403)
        Notice.objects.filter(pk=n.pk).update(must=True)
        self.assertEqual(self.ack(n, m5).status_code, 303)
        self.assertTrue(NoticeSeen.objects.filter(member=self.m5, acked=True).exists())


class MustTests(SeenBase):
    def test_member_cannot_turn_on_even_as_vice_principal(self):
        Member.objects.filter(pk=self.m4b.pk).update(position="교감")
        self.assertEqual(self.write(must="on").status_code, 403)
        self.assertFalse(Notice.objects.exists())
        n = self.made()
        self.assertEqual(self.edit(n, must="on").status_code, 403)
        n.refresh_from_db()
        self.assertFalse(n.must)

    def test_manager_can_turn_on_and_off(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on")
        self.assertTrue(n.must)
        self.assertEqual(self.edit(n, m4).status_code, 302)  # 체크를 빼고 저장 = 끔(내용이 같아도 판 기록)
        n.refresh_from_db()
        self.assertFalse(n.must)
        self.assertEqual(n.versions.count(), 2)
        self.assertContains(m4.get(f"{self.base}/{n.key}/"), "확인 필수")
        self.edit(n, m4, must="on")
        n.refresh_from_db()
        self.assertTrue(n.must)

    def test_writer_edit_keeps_must_set_by_manager(self):
        n = self.made()
        self.edit(n, self.as_("m4"), must="on")
        self.assertEqual(self.edit(n, 장소="체육관").status_code, 302)  # 일반 회원 폼에는 값이 없다
        n.refresh_from_db()
        self.assertTrue(n.must)

    def test_form_shows_toggle_by_role(self):
        self.assertContains(self.client.get(f"{self.base}/new"), 'name="must" disabled')
        self.assertNotContains(self.as_("m4").get(f"{self.base}/new"), 'name="must" disabled')

    def test_ack_flow(self):
        n = self.made(self.as_("m4"), must="on")
        page = self.as_("m5").get(f"{self.base}/{n.key}/")
        self.assertContains(page, "확인했어요")
        self.assertContains(self.as_("m5").get(self.base), "아직 안 봄")
        self.seen(n, self.as_("m5"))  # 열어 본 것만으로는 확인이 아니다
        self.assertContains(self.as_("m5").get(self.base), "아직 안 봄")
        r = self.ack(n, self.as_("m5"))
        self.assertEqual(r.status_code, 303)
        self.assertTrue(NoticeSeen.objects.get(member=self.m5, notice=n).acked)
        self.assertNotContains(self.as_("m5").get(self.base), "아직 안 봄")
        self.assertContains(self.as_("m5").get(f"{self.base}/{n.key}/"), "확인함")

    def test_ack_only_on_must(self):
        n = self.made()
        self.assertEqual(self.ack(n, self.as_("m5")).status_code, 400)
        self.assertFalse(NoticeSeen.objects.exists())

    def test_writer_and_outsider_manager_have_nothing_to_do(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on")
        self.assertNotContains(m4.get(self.base), "아직 안 봄")
        self.assertNotContains(m4.get(f"{self.base}/{n.key}/"), "확인했어요</button>")
        g5 = self.made(m4, must="on", aud="groups", groups=["g5"], title="오학년만")
        self.assertNotContains(self.client.get(self.base), "오학년만")
        self.assertContains(self.as_("m5").get(self.base), "아직 안 봄")
        self.assertTrue(g5.must)


class StatusTests(SeenBase):
    def test_status_only_for_writer_and_manager(self):
        n = self.made(self.client)  # m4b가 씀, 대상 전체 4명
        self.seen(n, self.as_("m5"))
        for who in (self.client, self.as_("m4")):
            page = who.get(f"{self.base}/{n.key}/")
            self.assertContains(page, "확인 현황")
            self.assertContains(page, "2/4명")  # 쓴 사람 + m5
            self.assertContains(page, "비밀선생")
            self.assertContains(page, "안 본 분께 보낼 문구 복사")
            self.assertContains(who.get(self.base), "확인 50%")
        self.assert_no_status(self.as_("m5").get(f"{self.base}/{n.key}/"))

    def test_receiver_sees_no_status_anywhere(self):
        writer = self.as_("m4")
        n = self.made(writer, must="on", title="확인 필수 안내")
        self.edit(n, writer, 장소="체육관", real="on")
        self.seen(n, self.as_("quiet"))
        self.ack(n, self.as_("quiet"))
        rec = self.as_("m5")
        urls = [f"{self.base}/{n.key}/", f"{self.base}/{n.key}/?v=1", self.base,
                f"/s/{self.hub.token}/today", f"/s/{self.hub.token}/today?show=school",
                f"/s/{self.hub.token}/bell", f"/s/{self.hub.token}/schedule"]
        with mock.patch("spaces.neis.schedule", return_value=[]), mock.patch("spaces.neis.meals", return_value=[]):
            for url in urls:
                r = rec.get(url)
                self.assertEqual(r.status_code, 200, url)
                body = r.content.decode()
                for mark in STATUS_MARKS:
                    self.assertNotIn(mark, body, url)
                self.assertIsNone(RATIO.search(body), url)
                self.assertNotIn("data-recheck", body, url)
            # 받는 사람이 현황 문구의 재료(쪽지 문구도 남 정보 없음)를 받지 않는다
            self.assertNotIn("비밀선생", rec.get(f"{self.base}/{n.key}/").content.decode())

    def test_total_is_current_audience_not_history(self):
        n = self.made(self.client, aud="groups", groups=["g4"])
        self.seen(n, self.as_("quiet"))
        page = self.client.get(f"{self.base}/{n.key}/")
        self.assertContains(page, "2/3명")  # g4 3명 중 쓴 사람 + 비밀선생
        Member.objects.filter(pk=self.quiet.pk).update(group="g5")  # 다른 그룹으로 옮김
        self.assertContains(self.client.get(f"{self.base}/{n.key}/"), "1/2명")
        Member.objects.create(space=self.hub, name="새선생", cls="4-4", group="g4")
        self.assertContains(self.client.get(f"{self.base}/{n.key}/"), "1/3명")

    def test_must_counts_only_acks(self):
        n = self.made(self.as_("m4"), must="on")
        self.seen(n, self.as_("m5"))
        page = self.as_("m4").get(f"{self.base}/{n.key}/")
        self.assertContains(page, "1/4명")
        self.assertContains(page, "열어 본 분 2명")
        self.ack(n, self.as_("m5"))
        self.assertContains(self.as_("m4").get(f"{self.base}/{n.key}/"), "2/4명")

    def test_copy_text_uses_site_url(self):
        n = self.made()
        with override_settings(SITE_URL="https://t.goteacher.xyz"):
            page = self.client.get(f"{self.base}/{n.key}/")
        self.assertContains(page, f"https://t.goteacher.xyz/n/{n.key}")
        self.assertContains(page, "쪽지용 링크 복사")


class RealChangeTests(SeenBase):
    def test_bell_only_to_those_who_saw_earlier(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.assertEqual(self.edit(n, 장소="체육관", real="on").status_code, 302)
        bells = self.bells(n, Notification.Kind.CHANGE)
        self.assertEqual([b.member_id for b in bells], [self.m5.pk])  # 비밀선생·관리자·쓴 사람 없음
        self.assertIn("장소 강당 → 체육관", bells[0].text)
        self.assertEqual(bells[0].link, f"/s/{self.hub.token}/board/{n.key}/")
        self.assertFalse(bells[0].pushed)

    def test_off_is_silent(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.edit(n, 장소="체육관")
        self.assertEqual(n.versions.count(), 2)
        self.assertFalse(self.bells(n).exists())
        self.assertFalse(n.versions.get(v=2).real)

    def test_on_without_change_makes_nothing(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.edit(n, real="on")
        self.assertEqual(n.versions.count(), 1)
        self.assertFalse(self.bells(n).exists())

    def test_seen_someone_now_outside_audience_gets_no_bell(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.edit(n, aud="groups", groups=["g4"], 장소="체육관", real="on")
        self.assertFalse(self.bells(n).filter(member=self.m5).exists())

    def test_writer_saw_it_so_manager_edit_tells_writer(self):
        n = self.made()
        self.edit(n, self.as_("m4"), 장소="체육관", real="on")
        self.assertEqual([b.member_id for b in self.bells(n, "change")], [self.m4b.pk])

    def test_recheck_count(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.seen(n, self.as_("quiet"))
        self.edit(n, 장소="체육관", real="on")
        page = self.client.get(f"{self.base}/{n.key}/")
        self.assertContains(page, "2차 수정 바뀐 부분 확인")
        self.assertContains(page, "0/2")
        self.seen(n, self.as_("m5"))
        self.assertContains(self.client.get(f"{self.base}/{n.key}/"), "1/2")
        self.edit(n, 준비물="실내화")  # 조용한 판도 v2 이후면 본 것으로 친다
        self.seen(n, self.as_("quiet"))
        self.assertContains(self.client.get(f"{self.base}/{n.key}/"), "2/2")

    def test_no_recheck_line_when_nobody_saw_earlier(self):
        n = self.made()
        self.edit(n, 장소="체육관", real="on")
        self.assertNotContains(self.client.get(f"{self.base}/{n.key}/"), "바뀐 부분 확인")

    def test_first_read_after_change_is_not_recheck(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.edit(n, 장소="체육관", real="on")
        self.seen(n, self.as_("quiet"))  # v1을 안 봤다: 재확인 분모 밖
        self.assertContains(self.client.get(f"{self.base}/{n.key}/"), "0/1")

    def test_seeing_latest_reads_the_change_bell(self):
        n = self.made()
        m5 = self.as_("m5")
        self.seen(n, m5)
        self.edit(n, 장소="체육관", real="on")
        self.assertFalse(self.bells(n)[0].read)
        self.seen(n, m5)
        self.assertTrue(self.bells(n)[0].read)

    def test_diff_box_marks_real(self):
        n = self.made()
        self.edit(n, 장소="체육관", real="on")
        self.assertContains(self.as_("m5").get(f"{self.base}/{n.key}/"), "내용 바뀜")
        self.edit(n, 준비물="실내화")
        self.assertNotContains(self.as_("m5").get(f"{self.base}/{n.key}/"), ">내용 바뀜<")

    def test_edit_form_has_toggle_and_originals(self):
        n = self.made()
        page = self.client.get(f"{self.base}/{n.key}/edit")
        self.assertContains(page, 'name="real"')
        self.assertContains(page, 'data-orig="강당"')
        self.assertNotContains(self.client.get(f"{self.base}/new"), 'name="real"')


class NewBellTests(SeenBase):
    def test_must_bells_everyone_but_writer(self):
        n = self.made(self.as_("m4"), must="on")
        got = set(self.bells(n, Notification.Kind.NOTICE).values_list("member_id", flat=True))
        self.assertEqual(got, {self.m4b.pk, self.m5.pk, self.quiet.pk})
        self.assertFalse(self.bells(n)[0].pushed)

    def test_must_bell_follows_audience(self):
        n = self.made(self.as_("m4"), must="on", aud="groups", groups=["g5"])
        self.assertEqual(list(self.bells(n).values_list("member_id", flat=True)), [self.m5.pk])

    def test_plain_notice_has_no_bell(self):
        n = self.made()
        self.assertFalse(self.bells(n).exists())
        self.assertContains(self.as_("m5").get(self.base), n.title)

    def test_flip_and_widen_never_duplicate(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on", aud="groups", groups=["g5"])
        self.edit(n, m4, must="on", aud="all")
        self.assertEqual(self.bells(n, "notice").count(), 3)
        n.refresh_from_db()
        self.edit(n, m4)  # 끔
        n.refresh_from_db()
        self.edit(n, m4, must="on")  # 다시 켬
        self.assertEqual(self.bells(n, "notice").count(), 3)

    def test_bell_row_shows_notice_and_opens_it(self):
        n = self.made(self.as_("m4"), must="on")
        m5 = self.as_("m5")
        panel = m5.get(f"/s/{self.hub.token}/bell")
        self.assertContains(panel, n.title)
        self.assertContains(panel, ">공지<")
        bell = self.bells(n).get(member=self.m5)
        r = m5.post(f"/s/{self.hub.token}/bell/{bell.key}")
        self.assertRedirects(r, f"{self.base}/{n.key}/", fetch_redirect_response=False)

    def test_deleting_notice_removes_bells(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on")
        m4.post(f"{self.base}/{n.key}/delete")
        self.assertFalse(Notification.objects.filter(kind="notice").exists())

    @override_settings(VAPID_PUBLIC_KEY="p", VAPID_PRIVATE_KEY="k", VAPID_SUBJECT="mailto:a@b.c")
    def test_digest_includes_notice_title_outside_class_only(self):
        n = self.made(self.as_("m4"), must="on", title="학예회 안내")
        PushSubscription.objects.create(space=self.hub, member=self.m5, endpoint="https://fcm.googleapis.com/x",
                                        p256dh="p", auth="a")
        Notification.objects.exclude(notice=n).update(read=True)  # 합칠 때 옮겨 온 알림이 첫 제목이 되지 않게
        seoul = ZoneInfo("Asia/Seoul")
        with mock.patch("collect.push.webpush") as send, mock.patch("collect.push.neis.day_off", return_value=False):
            self.assertEqual(push.digest(datetime(2026, 11, 3, 10, 0, tzinfo=seoul)), 0)  # 수업 중
            send.assert_not_called()
            self.assertGreaterEqual(push.digest(datetime(2026, 11, 3, 12, 25, tzinfo=seoul)), 1)
        self.assertTrue(any("학예회 안내" in c.kwargs["data"] for c in send.call_args_list))
        self.assertTrue(self.bells(n).get(member=self.m5).pushed)


class TodayTests(SeenBase):
    def setUp(self):
        super().setUp()
        for name in ("schedule", "meals"):
            p = mock.patch(f"spaces.neis.{name}", return_value=[])
            p.start()
            self.addCleanup(p.stop)
        self.today = f"/s/{self.hub.token}/today"

    def test_must_unseen_on_today_until_acked(self):
        n = self.made(self.as_("m4"), must="on", title="확인할 공지")
        m5 = self.as_("m5")
        page = m5.get(self.today)
        self.assertContains(page, "확인할 공지")
        self.assertContains(page, "아직 안 봄")
        self.assertContains(page, f"/board/{n.key}/")
        self.ack(n, m5)
        self.assertNotContains(m5.get(self.today), "아직 안 봄")

    def test_not_for_writer_or_plain_or_outside(self):
        m4 = self.as_("m4")
        self.made(m4, must="on", title="쓴 사람 것")
        self.assertNotContains(m4.get(self.today), "아직 안 봄")
        self.made(title="일반 공지")
        self.made(m4, must="on", title="오학년 것", aud="groups", groups=["g5"])
        page = self.as_("quiet").get(self.today)
        self.assertNotContains(page, "일반 공지")
        self.assertNotContains(page, "오학년 것")
        self.assertContains(page, "쓴 사람 것")

    def test_school_toggle_title_only_for_receiver_ratio_for_writer(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on", title="전체 확인 공지")
        self.ack(n, self.as_("m5"))
        rec = self.as_("quiet").get(f"{self.today}?show=school")
        self.assertContains(rec, "전체 확인 공지")
        self.assertNotContains(rec, "2/4")
        self.assertContains(m4.get(f"{self.today}?show=school"), "2/4")

    def test_school_toggle_hides_notice_outside_audience(self):
        self.made(self.as_("m4"), must="on", title="오학년 것", aud="groups", groups=["g5"])
        self.assertNotContains(self.as_("quiet").get(f"{self.today}?show=school"), "오학년 것")

    def test_grade_space_today_unchanged(self):
        school = School.objects.create(neis_atpt="B10", neis_code="1111111", name="다른초")
        far = Space.objects.create(school=school, grade=1, name="다른초 1학년")
        u = get_user_model().objects.create(username="google:i2far")
        Member.objects.create(space=far, user=u, name="남", cls="1-1")
        c = Client()
        c.force_login(u)
        self.assertEqual(c.get(f"/s/{far.token}/today").status_code, 200)


class LinkTests(SeenBase):
    def test_anonymous_goes_to_login_without_content(self):
        n = self.made(title="비밀 제목")
        r = Client().get(f"/n/{n.key}")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], f"/start?next=/n/{n.key}")
        self.assertIn("noindex", r["X-Robots-Tag"])
        page = Client().get(r["Location"])
        self.assertNotContains(page, "비밀 제목")
        self.assertContains(page, f"/n/{n.key}")  # 로그인 뒤 돌아오는 길

    def test_anonymous_cannot_tell_token_exists(self):
        n = self.made()
        a, b = Client().get(f"/n/{n.key}"), Client().get("/n/없는토큰")
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(b.status_code, 302)

    def test_member_goes_to_detail(self):
        n = self.made()
        r = self.as_("m5").get(f"/n/{n.key}")
        self.assertRedirects(r, f"{self.base}/{n.key}/", fetch_redirect_response=False)
        self.assertIn("noindex", r["X-Robots-Tag"])

    def test_outside_or_stranger_404(self):
        n = self.made(aud="groups", groups=["g4"])
        r = self.as_("m5").get(f"/n/{n.key}")
        self.assertEqual(r.status_code, 404)
        self.assertIn("noindex", r["X-Robots-Tag"])
        stranger = get_user_model().objects.create(username="google:st")
        c = Client()
        c.force_login(stranger)
        self.assertEqual(c.get(f"/n/{n.key}").status_code, 404)
        self.assertEqual(c.get("/n/없는토큰").status_code, 404)

    def test_open_redirect_impossible(self):
        for token in ("%5C%5Cevil.com", "https:%2F%2Fevil.com", "@evil.com"):
            r = Client().get(f"/n/{token}")
            if r.status_code == 302:
                self.assertTrue(r["Location"].startswith("/start?next=/n/"), r["Location"])

    def test_post_not_allowed(self):
        self.assertEqual(self.as_("m5").post("/n/x").status_code, 405)


class QueryCountTests(SeenBase):
    def people(self, n):
        Member.objects.bulk_create([Member(space=self.hub, name=f"교사{i}", cls=f"1-{i}", group="g1") for i in range(n)])

    def seed(self, count):
        for i in range(count):
            Notice.objects.create(space=self.hub, title=f"공지{i}", by=self.m4, by_name="송민재", must=bool(i % 2))
        for n in Notice.objects.all():
            n.versions.get_or_create(v=1, defaults={"fields": {"제목": n.title}, "by": "송민재"})

    def count(self, client, url):
        with mock.patch("spaces.neis.schedule", return_value=[]), mock.patch("spaces.neis.meals", return_value=[]):
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(client.get(url).status_code, 200)
        return len(q)

    def test_list_detail_today_independent_of_people_and_notices(self):
        self.seed(3)
        m4, m5 = self.as_("m4"), self.as_("m5")
        key = Notice.objects.first().key
        urls = [self.base, f"{self.base}/{key}/", f"/s/{self.hub.token}/today", f"/s/{self.hub.token}/today?show=school"]
        before = {(who, url): self.count(c, url) for who, c in (("m4", m4), ("m5", m5)) for url in urls}
        self.people(200)
        self.seed(50)
        NoticeSeen.objects.bulk_create([NoticeSeen(notice=n, member=self.m5, v=1) for n in Notice.objects.all()])
        after = {(who, url): self.count(c, url) for who, c in (("m4", m4), ("m5", m5)) for url in urls}
        self.assertEqual(before, after)

    def test_must_to_200_people_is_bulk(self):
        self.people(200)
        m4 = self.as_("m4")
        with CaptureQueriesContext(connection) as q:
            n = self.made(m4, must="on")
        inserts = [x for x in q if x["sql"].startswith("INSERT") and "collect_notification" in x["sql"]]
        self.assertLess(len(inserts), 5)  # 사람마다 한 번이 아니라 묶음으로(SQLite는 변수 한도로 몇 번 나뉜다)
        self.assertEqual(self.bells(n).count(), 203)


class OpenSchoolStillWorks(SeenBase):
    def test_reopen_keeps_notices_and_seen(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        open_school(self.school, timezone.localdate() + timedelta(days=30), self.m4)
        self.assertTrue(NoticeSeen.objects.filter(notice=n, member=self.m5).exists())


class FollowUpTests(SeenBase):
    """QA·보안 검토 반영."""

    def test_narrowing_drops_unread_bell_and_digest_filters_at_send(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on", title="처음 제목")
        PushSubscription.objects.create(space=self.hub, member=self.m5, endpoint="https://fcm.googleapis.com/x",
                                        p256dh="p", auth="a")
        Notification.objects.exclude(notice=n).update(read=True)
        kept = self.bells(n).get(member=self.m5)
        self.edit(n, m4, must="on", aud="groups", groups=["g4"], title="민감한 새 제목")
        self.assertFalse(Notification.objects.filter(pk=kept.pk).exists())
        # 지우는 사이를 빠져나간 🔔(그룹 이동 등)도 보내는 시점에 거른다
        Notification.objects.create(space=self.hub, member=self.m5, notice=n, kind="change", day=timezone.localdate(),
                                    text="x", link="/")
        seoul = ZoneInfo("Asia/Seoul")
        with mock.patch("collect.push.webpush") as send, mock.patch("collect.push.neis.day_off", return_value=False):
            push.digest(datetime(2026, 11, 3, 12, 25, tzinfo=seoul))
        self.assertFalse(any("민감한 새 제목" in c.kwargs["data"] for c in send.call_args_list))

    def test_must_audience_only_manager(self):
        n = self.made(self.client)
        self.edit(n, self.as_("m4"), must="on")
        n.refresh_from_db()
        self.assertEqual(self.edit(n, aud="groups", groups=["g4"]).status_code, 403)
        n.refresh_from_db()
        self.assertEqual(n.audience, "all")
        self.assertContains(self.client.get(f"{self.base}/{n.key}/edit"), "받는 곳은 학교 관리자만")
        # 잠긴 칸은 제출되지 않는다: aud 없이 저장해도 대상 그대로
        ver = n.versions.order_by("-v").first()
        r = self.client.post(f"{self.base}/{n.key}/edit", {"title": n.title, "body": ver.body, "base_v": ver.v, "장소": "강당",
                                                           "일시": ver.fields["일시"]})
        self.assertEqual(r.status_code, 302)
        n.refresh_from_db()
        self.assertEqual(n.audience, "all")
        m4 = self.as_("m4")
        self.assertEqual(self.edit(n, m4, must="on", aud="groups", groups=["g4"]).status_code, 302)

    def test_must_content_change_forces_real_and_recheck(self):
        m4 = self.as_("m4")
        n = self.made(m4, must="on")
        self.seen(n, self.as_("m5"))
        self.edit(n, m4, must="on", 장소="체육관")  # real 없이
        self.assertTrue(n.versions.get(v=2).real)
        self.assertContains(m4.get(f"{self.base}/{n.key}/"), "2차 수정 바뀐 부분 확인")
        self.edit(n, m4, must="on", title="제목만 바꿈")  # 본문·항목이 아니면 선택
        self.assertFalse(n.versions.get(v=3).real)
        plain = self.made(m4)
        self.edit(plain, m4, 장소="체육관")
        self.assertFalse(plain.versions.get(v=2).real)

    def test_flip_records_version_without_change_bell(self):
        m4 = self.as_("m4")
        n = self.made(m4)
        self.seen(n, self.as_("m5"))
        self.edit(n, m4, must="on")
        self.assertEqual(n.versions.get(v=2).fields["확인 필수"], "켬")
        self.assertFalse(n.versions.get(v=2).real)
        self.assertEqual(self.bells(n).get(member=self.m5).kind, "notice")

    def test_group_hint_on_form(self):
        self.assertContains(self.client.get(f"{self.base}/new"), "소속을 골라도 완전한 비공개는 아니에요")

    def test_manager_erases_old_version(self):
        m4 = self.as_("m4")
        n = self.made(m4, body="김학생 010-1234-5678")
        self.seen(n, self.as_("m5"))
        self.edit(n, m4, 장소="체육관", body="지운 뒤", real="on")
        self.edit(n, m4, 준비물="실내화")
        url = f"{self.base}/{n.key}/v/1/erase"
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.as_("m5").post(url).status_code, 403)
        self.assertEqual(m4.post(f"{self.base}/{n.key}/v/3/erase").status_code, 400)
        self.assertContains(m4.get(f"{self.base}/{n.key}/?v=1"), "이 내용 지우기")
        self.assertNotContains(m4.get(f"{self.base}/{n.key}/"), "이 내용 지우기")
        self.assertNotContains(self.as_("m5").get(f"{self.base}/{n.key}/?v=1"), "이 내용 지우기")
        self.assertEqual(m4.post(url).status_code, 303)
        ver = n.versions.get(v=1)
        self.assertEqual((ver.fields, ver.body, ver.erased), ({}, "", True))
        for v in (1, 2):
            page = self.as_("m5").get(f"{self.base}/{n.key}/?v={v}")
            self.assertNotContains(page, "김학생")
            self.assertNotContains(page, "강당")
        self.assertContains(self.as_("m5").get(f"{self.base}/{n.key}/?v=1"), "관리자가 지웠어요")
        self.assertContains(self.as_("m5").get(f"{self.base}/{n.key}/"), "관리자가 지움")
        bell = self.bells(n, "change").get(member=self.m5)
        self.assertNotIn("강당", bell.text)
        self.assertNotIn("체육관", bell.text)

    def test_real_change_shows_in_today_feed_only_for_those_who_saw(self):
        n = self.made()
        self.seen(n, self.as_("m5"))
        self.edit(n, 장소="체육관", real="on")
        today = f"/s/{self.hub.token}/today"
        with mock.patch("spaces.neis.schedule", return_value=[]), mock.patch("spaces.neis.meals", return_value=[]):
            self.assertContains(self.as_("m5").get(today), "장소 강당 → 체육관")
            self.assertNotContains(self.as_("quiet").get(today), "강당")
            self.edit(n, 준비물="실내화")
            self.assertEqual(self.as_("m5").get(today).status_code, 200)

    def test_today_title(self):
        self.made(self.as_("m4"), must="on")
        with mock.patch("spaces.neis.schedule", return_value=[]), mock.patch("spaces.neis.meals", return_value=[]):
            self.assertContains(self.as_("m5").get(f"/s/{self.hub.token}/today"), "아직 확인 안 했어요")
