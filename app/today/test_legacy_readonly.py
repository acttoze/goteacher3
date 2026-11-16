"""V3 이관 학교의 구 데이터 보기 전용.

불변식
- LEGACY_READONLY가 켜져 있고 공간이 이관 학교(LegacyMap kind=school의 학교·기관 공간)면 일정·복무·게시판 쓰기 주소가 모두 403
  (관리자도). 데이터는 바뀌지 않는다.
- 읽기·첨부 내려받기·공지 '확인했어요'·취합은 그대로.
- 화면에 쓰기 단추·입력(판 ＋, 고치기, 복무 지우기, 본문·첨부·댓글 입력, 표 붙여넣기, 공지 쓰기·고치기·지우기)이 없고
  안내 한 줄 + 구 사이트 링크(새 탭)가 있다.
- 이관 아닌 공간, 또는 설정을 끄면 아무 변화 없음.
"""
from django.test import override_settings

from legacy.models import LegacyMap
from notices.models import Notice
from spaces.models import Attendance, School, Space

from .models import Event, EventComment
from .test_event_more import MoreBase, up
from .test_school import TODAY, UNTIL

HX = {"HX-Request": "true"}
LINE = "일정·복무·게시판 쓰기는 예전 고티처에서 해 주세요"
SITE = "https://old.example.test"
ON = override_settings(LEGACY_READONLY=True, LEGACY_SITE_URL=SITE)


class LegacyBase(MoreBase):
    def setUp(self):
        super().setUp()
        self.e = self.filled(self.t41, "all")  # 본문·첨부·댓글이 있는 일정(설정 켜기 전에 만든다)
        self.f, self.cm = self.e.files.get(), self.e.comments.get()
        self.a = Attendance.objects.create(space=self.hub, date=TODAY, name="오일", kind="출장", by=self.t51, member=self.t51)
        r = self.as_(self.boss).post(f"{self.base}/board/new", {"title": "학예회", "body": "본문", "aud": "all", "must": "on"})
        self.assertEqual(r.status_code, 302)
        self.n = Notice.objects.get()
        LegacyMap.objects.create(kind="school", legacy_id=self.school.neis_code, obj_id=self.school.pk)

    def writes(self):
        """(주소, 보낼 값, 방법) — 막혀야 하는 쓰기 전부."""
        e, b, n = self.e, self.base, self.n
        week = {"view": "week", "mode": "week", "w": "2026-10-26", "tab": "school"}
        return [
            (f"{b}/today/parse", {"line": "오늘 회의"}, "post"),
            (f"{b}/today/events", {"line": "오늘 2블록 회의", **week}, "post"),
            (self.url(e), {"date": TODAY.isoformat(), "slot": "", "place": "", "scope": "all", **week}, "post"),
            (self.url(e, "/delete"), week, "post"),
            (self.url(e, "/body"), {"body": "바꿈"}, "post"),
            (self.url(e, "/files"), {"attach": [up("새.pdf")]}, "post"),
            (self.url(e, f"/f/{self.f.key}/delete"), {}, "post"),
            (self.url(e, "/comments"), {"body": "새 댓글"}, "post"),
            (self.url(e, f"/c/{self.cm.key}/delete"), {}, "post"),
            (f"{b}/today/duty", {**week, "line": "사일 출장", "date": TODAY.isoformat(), "scope": "duty"}, "post"),
            (f"{b}/today/duty/{self.a.key}/delete", week, "post"),
            (f"{b}/schedule/paste", {}, "get"),
            (f"{b}/schedule/paste", {"text": "x"}, "post"),
            (f"{b}/schedule/paste/preview", {"text": "x"}, "post"),
            (f"{b}/school/attendance", {}, "get"),
            (f"{b}/school/attendance", {"text": "오일 출장"}, "post"),
            (f"{b}/school/attendance/preview", {"text": "오일 출장"}, "post"),
            (f"{b}/school/attendance/clear", {"date": TODAY.isoformat()}, "post"),
            (f"{b}/board/new", {}, "get"),
            (f"{b}/board/new", {"title": "새 공지", "body": "x", "aud": "all"}, "post"),
            (f"{b}/board/{n.key}/edit", {}, "get"),
            (f"{b}/board/{n.key}/edit", {"title": "바꿈", "body": "x", "aud": "all", "base_v": 1}, "post"),
            (f"{b}/board/{n.key}/delete", {}, "post"),
            (f"{b}/board/{n.key}/v/1/erase", {}, "post"),
        ]

    def snapshot(self):
        e = Event.objects.get(pk=self.e.pk)
        return (Event.objects.count(), e.body, e.date, e.files.count(), EventComment.objects.count(), Attendance.objects.count(),
                Notice.objects.count(), self.n.versions.count(), list(self.n.versions.values_list("erased", flat=True)))


@ON
class BlockedTests(LegacyBase):
    def test_every_write_is_403_for_manager_and_writer(self):
        before = self.snapshot()
        for who in (self.boss, self.t41, self.t51):
            c = self.as_(who)
            for url, data, how in self.writes():
                r = getattr(c, how)(url, data, headers=HX)
                self.assertEqual(r.status_code, 403, (who.name, how, url))
                if who == self.boss:  # 관리자는 다른 권한 관문을 다 지나 V3 관문에서 막힌다: 열린 탭 안내(gt.js)용 표시
                    self.assertEqual((r["X-GT-Legacy-RO"], r["X-GT-Legacy-Site"]), ("1", SITE), url)
                    self.assertEqual(r.content.decode(), LINE + ".")
        self.assertEqual(self.snapshot(), before)

    def test_other_403_has_no_legacy_mark(self):
        """다른 까닭의 403(관리자 아님)에는 V3 표시·문구가 없다. gt.js가 엉뚱한 안내를 띄우지 않게."""
        r = self.as_(self.t41).post(f"{self.base}/school/members/{self.t42.key}/manager", {"on": "1"}, headers=HX)
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("X-GT-Legacy-RO", r)
        self.assertNotIn(LINE, r.content.decode())

    def test_org_space_in_map_is_blocked_too(self):
        org = School.objects.create(neis_atpt="J10", neis_code="", name="교육청", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=org, kind="org", grade=0, name="교육청")
        boss = self.seat("과장", "기관장", "", manager=True, space=hub)
        LegacyMap.objects.create(kind="school", legacy_id="o1", obj_id=org.pk)
        r = self.as_(boss).post(f"/s/{hub.token}/today/events", {"line": "오늘 회의", "view": "week"}, headers=HX)
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(self.as_(boss).get(f"/s/{hub.token}/schedule"), 'class="cell-add"')

    def test_reads_ack_and_new_features_still_work(self):
        c = self.as_(self.t42)
        for path in ("/today", "/schedule", "/schedule?mode=month", "/schedule?mode=day", "/board", f"/board/{self.n.key}/",
                     "/c/", "/c/new", "/settings", "/school"):
            self.assertEqual(c.get(self.base + path).status_code, 200, path)
        self.assertEqual(c.get(self.url(self.e, "/more")).status_code, 200)
        d = c.get(self.url(self.e, f"/f/{self.f.key}"))
        self.assertEqual(d.status_code, 200)
        self.assertEqual(c.post(f"{self.base}/board/{self.n.key}/seen").status_code, 204)
        self.assertEqual(c.post(f"{self.base}/board/{self.n.key}/ack").status_code, 303)
        self.assertTrue(self.n.seen.filter(member=self.t42, acked=True).exists())


@ON
class ScreenTests(LegacyBase):
    def assertLine(self, html):
        self.assertIn(LINE, html)
        self.assertIn(f'href="{SITE}" target="_blank" rel="noopener"', html)

    def test_schedule_week_day_month_have_no_write_entry(self):
        for mode in ("week", "day", "month"):
            html = self.as_(self.boss).get(f"{self.base}/schedule", {"mode": mode}).content.decode()
            self.assertNotIn('class="cell-add"', html, mode)
            self.assertNotIn("data-cell data-date", html, mode)
            self.assertNotIn("표 붙여넣기", html)
            self.assertNotIn(f"/today/e/{self.e.key}/delete", html)  # 고치기 대화상자 대신 읽기
            self.assertNotIn(f"/today/duty/{self.a.key}/delete", html)
            self.assertIn(self.e.title, html)
            self.assertLine(html)

    def test_today_board_has_no_plus(self):
        html = self.as_(self.boss).get(f"{self.base}/today").content.decode()
        self.assertNotIn('class="cell-add"', html)
        self.assertNotIn("칸의 ＋를 누르면", html)
        self.assertLine(html)

    def test_event_panel_reads_only(self):
        html = self.as_(self.boss).get(self.url(self.e, "/more")).content.decode()
        for gone in ("/body", "/files", "/comments", "/delete", "댓글 올리기", "본문 저장", "파일 올리기"):
            self.assertNotIn(gone, html)
        self.assertIn("비밀본문", html)
        self.assertIn("비밀댓글", html)
        self.assertIn(f"/f/{self.f.key}", html)  # 받기는 그대로
        self.assertLine(html)

    def test_notices_list_and_detail(self):
        html = self.as_(self.boss).get(f"{self.base}/board").content.decode()
        self.assertNotIn("공지 쓰기", html)
        self.assertNotIn("/board/new", html)
        self.assertLine(html)
        html = self.as_(self.boss).get(f"{self.base}/board/{self.n.key}/").content.decode()
        self.assertNotIn(f"/board/{self.n.key}/edit", html)
        self.assertNotIn(f"/board/{self.n.key}/delete", html)
        self.assertLine(html)

    def test_school_page_hides_attendance_input(self):
        html = self.as_(self.boss).get(f"{self.base}/school").content.decode()
        self.assertNotIn(f"{self.base}/school/attendance", html)
        self.assertLine(html)


class UntouchedTests(LegacyBase):
    """설정을 끄거나 이관 아닌 공간이면 그대로."""

    def check_open(self):
        html = self.as_(self.boss).get(f"{self.base}/schedule").content.decode()
        self.assertIn('class="cell-add"', html)
        self.assertIn("표 붙여넣기", html)
        self.assertNotIn(LINE, html)
        self.assertNotIn(LINE, self.as_(self.boss).get(f"{self.base}/board").content.decode())
        self.assertNotIn(LINE, self.as_(self.boss).get(f"{self.base}/today").content.decode())
        self.assertIn("댓글 올리기", self.as_(self.boss).get(self.url(self.e, "/more")).content.decode())
        r = self.as_(self.t41).post(f"{self.base}/today/events", {"line": "오늘 2블록 회의", "view": "week"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.as_(self.t41).post(self.url(self.e, "/comments"), {"body": "또"}, headers=HX).status_code, 200)
        self.assertEqual(self.as_(self.boss).post(f"{self.base}/board/{self.n.key}/delete").status_code, 302)

    @override_settings(LEGACY_READONLY=False)
    def test_setting_off(self):
        self.check_open()

    @ON
    def test_not_migrated_school(self):
        LegacyMap.objects.all().delete()
        self.check_open()

    @ON
    def test_other_school_in_map_does_not_lock_this_one(self):
        LegacyMap.objects.filter(kind="school").update(obj_id=self.school.pk + 999)
        self.check_open()

    @ON
    def test_other_space_still_404(self):
        s = School.objects.create(neis_atpt="C10", neis_code="3", name="옆초", plan="school", paid_until=UNTIL)
        hub = Space.objects.create(school=s, kind="school", grade=0, name="옆초")
        boss = self.seat("교무실", "옆부장", "office", manager=True, space=hub)
        self.assertEqual(self.as_(boss).get(self.url(self.e, "/more").replace(self.base, f"/s/{hub.token}")).status_code, 404)
        self.assertEqual(self.as_(boss).post(f"{self.base}/today/events", {"line": "x"}).status_code, 404)
