"""QC 일정 탭(2026-11-06) 독립 검증(gt-qa). test_schedule_channels*.py에 없는 경계만.

- 쓰기 뒤 탭·모드·날짜(w): 일정 고치기·지우기, 복무 넣기·지우기를 세 모드에서. htmx 없이 저장해도.
- 칸 추가 범위: 소속 없는 사람의 그룹 값, 다른 그룹 사람의 그룹 값.
- 취합 링크: 대상 아닌 회원은 세 탭·세 모드·htmx 응답·오늘·인쇄 어디에도 받는 링크 토큰이 없다. 그룹 칸 줄은 그 그룹에만.
- 복무: 과거 31일 경계, 이름 일부·없는 유형 거절, 다른 공간 회원 404, 로그에 사유·지우기 key 없음.
- 남의 개인 일정: 칸 추가(블록 칸) 토스트·그룹·개인 탭에서도 흔적 없음.
- 블록 이름 두 번 안 나옴: 일간 학교 탭, 그룹·개인 탭 세 모드.
- 그룹 탭: 같은 소속 다른 선생님 일정이 보인다. 개인·그룹 탭 쿼리 수가 건수와 무관.
"""
import io
import logging
import re
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from collect.models import Collection, Field, Row
from config.logsafe import SafeErrorFormatter
from spaces.models import Attendance, Member, School, Space

from .models import ALL, ME, Event
from .test_schedule_channels import card, grid_of, rows_of

User = get_user_model()
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 29, 9, 5, tzinfo=KST)  # 목요일
TODAY = NOW.date()
SCHED = [{"ymd": "20261029", "name": "독서주간 골든벨"}]
HX = {"HX-Request": "true"}
MODES = ("day", "week", "month")
# 모드마다 보고 있는 날(w): 일간 그날, 주간 월요일, 월간 1일
W = {"day": "2026-11-03", "week": "2026-11-02", "month": "2026-11-01"}


class Base(TestCase):
    def setUp(self):
        for p in (mock.patch("django.utils.timezone.now", return_value=NOW),
                  mock.patch("spaces.neis.meals", return_value=[]),
                  mock.patch("spaces.neis.schedule", return_value=SCHED)):
            p.start()
            self.addCleanup(p.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7538100", name="탭검증초", plan="school", paid_until=date(2099, 1, 1))
        self.hub = Space.objects.create(school=school, kind="school", grade=0, name="탭검증초")
        self.boss = self.member("부장", "교무실", "office", is_manager=True)
        self.t4 = self.member("사일", "4-1", "g4")
        self.t4b = self.member("사이", "4-2", "g4")
        self.t5 = self.member("오일", "5-1", "g5")
        self.base = f"/s/{self.hub.token}"

    def member(self, name, cls, group, **kw):
        u = User.objects.create(username=f"google:{name}", first_name=name)
        return Member.objects.create(space=self.hub, user=u, name=name, cls=cls, group=group, **kw)

    def as_(self, m):
        c = Client()
        c.force_login(m.user)
        return c

    def ev(self, by, title, scope=ALL, slot="", d=TODAY, **kw):
        return Event.objects.create(space=self.hub, by=by, title=title, scope=scope, slot=slot, slot_end=kw.pop("slot_end", slot), date=d, **kw)

    def page(self, m, mode, tab="school", **kw):
        return self.as_(m).get(f"{self.base}/schedule", {"mode": mode, "tab": tab, **kw}).content.decode()

    def assert_state(self, html, tab, mode, w, msg=""):
        self.assertIn(f'data-mode="{mode}"', html, msg)
        self.assertIn(f'data-w="{w}"', html, msg)
        self.assertIn(f'data-tab="{tab}"', html, msg)


class KeepStateAfterWriteTests(Base):
    """기준 4: 칸 추가·수정·삭제 뒤에도 탭·모드·날짜 유지(추가는 구현 테스트가 봄, 날짜·수정·삭제·복무는 여기)."""

    def test_event_edit_and_delete_keep_tab_mode_date(self):
        c = self.as_(self.t4)
        for tab, scope in (("school", ALL), ("group", "g4"), ("me", ME)):
            for mode in MODES:
                e = self.ev(self.t4, f"고칠 {tab}{mode}", scope, "b1", d=date(2026, 11, 3))
                state = {"view": "week", "mode": mode, "w": W[mode], "tab": tab}
                r = c.post(f"{self.base}/today/e/{e.key}", {**state, "date": "2026-11-03", "slot": "b2", "place": "", "scope": scope}, headers=HX)
                self.assertEqual(r.status_code, 200, (tab, mode))
                self.assert_state(r.content.decode(), tab, mode, W[mode], ("edit", tab, mode))
                r = c.post(f"{self.base}/today/e/{e.key}/delete", state, headers=HX)
                self.assertEqual(r.status_code, 200, (tab, mode))
                self.assert_state(r.content.decode(), tab, mode, W[mode], ("delete", tab, mode))

    def test_cell_add_keeps_date_in_every_mode(self):
        for tab, scope in (("school", ALL), ("group", "g4"), ("me", ME)):
            for mode in MODES:
                r = self.as_(self.t4).post(f"{self.base}/today/events", {"view": "week", "mode": mode, "w": W[mode], "tab": tab,
                                                                         "line": "날짜 유지", "date": "2026-11-03", "slot": "", "scope": scope}, headers=HX)
                self.assert_state(r.content.decode(), tab, mode, W[mode], (tab, mode))

    def test_duty_add_and_delete_keep_mode_date(self):
        c = self.as_(self.t4)
        for mode in MODES:
            state = {"view": "week", "mode": mode, "w": W[mode], "tab": "school"}
            r = c.post(f"{self.base}/today/duty", {**state, "line": "오일 출장", "date": "2026-11-03", "slot": "", "scope": "duty"}, headers=HX)
            self.assertEqual(r.status_code, 200, mode)
            self.assert_state(r.content.decode(), "school", mode, W[mode], ("duty add", mode))
            a = Attendance.objects.get()
            r = c.post(f"{self.base}/today/duty/{a.key}/delete", state, headers=HX)
            self.assert_state(r.content.decode(), "school", mode, W[mode], ("duty delete", mode))
            self.assertFalse(Attendance.objects.exists())

    def test_without_js_duty_goes_back_to_same_tab_mode_date(self):
        c = self.as_(self.t4)
        r = c.post(f"{self.base}/today/duty", {"view": "week", "mode": "month", "w": "2026-11-01", "tab": "school",
                                              "line": "오일 출장", "date": "2026-11-03", "slot": ""})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r["Location"], f"{self.base}/schedule?w=2026-11-01&mode=month&tab=school")
        a = Attendance.objects.get()
        r = c.post(f"{self.base}/today/duty/{a.key}/delete", {"view": "week", "mode": "day", "w": "2026-11-03", "tab": "school"})
        self.assertEqual(r["Location"], f"{self.base}/schedule?w=2026-11-03&mode=day&tab=school")

    def test_bad_tab_value_falls_back_to_school(self):
        r = self.as_(self.t4).post(f"{self.base}/today/events", {"view": "week", "mode": "week", "w": "2026-10-26", "tab": "<x>",
                                                                 "line": "이상한 탭", "date": "2026-10-27", "slot": "", "scope": ALL}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-tab="school"', r.content.decode())
        self.assertNotIn("&lt;x&gt;", r.content.decode())


class CellScopeTests(Base):
    """기준 5: 그룹 값은 내 소속만, 틀린 값 400."""

    def post(self, m, scope, tab="group"):
        return self.as_(m).post(f"{self.base}/today/events", {"view": "week", "mode": "week", "w": "2026-10-26", "tab": tab,
                                                             "line": "범위 시험", "date": "2026-10-27", "slot": "", "scope": scope}, headers=HX)

    def test_no_group_member_cannot_post_any_group(self):
        m = self.member("무소속", "지원", "")
        for g in ("g4", "office", "g1"):
            self.assertEqual(self.post(m, g).status_code, 400, g)
        self.assertEqual(self.post(m, ALL, "school").status_code, 200)
        self.assertEqual(self.post(m, ME, "me").status_code, 200)
        self.assertEqual(sorted(Event.objects.values_list("scope", flat=True)), sorted([ALL, ME]))

    def test_manager_cannot_post_other_group_either(self):
        self.assertEqual(self.post(self.boss, "g4").status_code, 400)
        self.assertEqual(self.post(self.boss, "office").status_code, 200)
        self.assertEqual(Event.objects.get().scope, "office")

    def test_group_cell_add_shows_in_colleague_group_tab_only(self):
        self.post(self.t4, "g4")
        self.assertIn("범위 시험", grid_of(self.page(self.t4b, "week", tab="group", w="2026-10-26")))
        for m, tab in ((self.t5, "group"), (self.t5, "school"), (self.boss, "group"), (self.t4b, "school"), (self.t4b, "me")):
            self.assertNotIn("범위 시험", grid_of(self.page(m, "week", tab=tab, w="2026-10-26")), (m.name, tab))


class GroupTabContentTests(Base):
    """기준 3: 그룹 탭 = 내 소속 범위 일정(같은 소속 다른 선생님 것 포함)."""

    def test_colleague_group_event_visible(self):
        self.ev(self.t4b, "사이 그룹일", "g4", "b1")
        self.ev(self.t4b, "사이 혼자일", ME, "b1")
        for mode in MODES:
            grid = grid_of(self.page(self.t4, mode, tab="group"))
            self.assertIn("사이 그룹일", grid, mode)
            self.assertNotIn("사이 혼자일", self.page(self.t4, mode, tab="group"), mode)
            self.assertNotIn("사이 혼자일", self.page(self.t4, mode, tab="me"), mode)

    def test_manager_group_tab_is_own_group_only(self):
        self.ev(self.t4, "사학년만", "g4", "b1")
        self.ev(self.boss, "교무실만", "office", "b1")
        for mode in MODES:
            grid = grid_of(self.page(self.boss, mode, tab="group"))
            self.assertIn("교무실만", grid, mode)
            self.assertNotIn("사학년만", grid, mode)


class CollectLinkTests(Base):
    """기준 6: 대상 아닌 회원 응답 어디에도 받는 링크 토큰이 없다."""

    def setUp(self):
        super().setUp()
        self.c = Collection.objects.create(space=self.hub, created_by=self.t5, title="오학년 조사", source=Collection.Source.SHEET,
                                           unit_head=["학년반"], due=datetime(2026, 10, 29, 15, 0, tzinfo=KST))
        Field.objects.create(collection=self.c, pos=0, name="칸", kind="number")
        self.r = Row.objects.create(collection=self.c, pos=0, unit="5-1", who="오일", member=self.t5)
        self.secrets = (self.c.link_token, self.r.key, self.c.key)

    def clean(self, html, where):
        for s in self.secrets:
            self.assertNotIn(s, html, where)

    def test_every_tab_mode_and_surface(self):
        c = self.as_(self.t4)
        for tab in ("school", "group", "me"):
            for mode in MODES:
                r = c.get(f"{self.base}/schedule", {"mode": mode, "tab": tab})
                self.assertEqual(r.status_code, 200)
                html = r.content.decode()
                self.clean(html, (tab, mode))
                if tab == "school":
                    self.assertIn("오학년 조사", grid_of(html), mode)
        for path in ("/today", "/schedule/print?view=week&d=2026-10-26", "/schedule/print?view=month&d=2026-10-01"):
            self.clean(c.get(self.base + path).content.decode(), path)

    def test_htmx_replies_after_writes(self):
        c = self.as_(self.t4)
        for mode in MODES:
            state = {"view": "week", "mode": mode, "w": {"day": TODAY.isoformat(), "week": "2026-10-26", "month": "2026-10-01"}[mode], "tab": "school"}
            r = c.post(f"{self.base}/today/events", {**state, "line": "남의 마감 옆", "date": TODAY.isoformat(), "slot": "", "scope": ALL}, headers=HX)
            self.clean(r.content.decode(), ("add", mode))
            r = c.post(f"{self.base}/today/duty", {**state, "line": "오일 출장", "date": TODAY.isoformat(), "slot": ""}, headers=HX)
            self.clean(r.content.decode(), ("duty", mode))
            self.assertIn("오학년 조사", r.content.decode(), mode)

    def test_group_cell_row_link_only_for_that_group(self):
        c = Collection.objects.create(space=self.hub, created_by=self.t5, title="사학년 칸", source=Collection.Source.SHEET,
                                      unit_head=["학년반"], due=datetime(2026, 10, 29, 16, 0, tzinfo=KST))
        Field.objects.create(collection=c, pos=0, name="칸", kind="number")
        r = Row.objects.create(collection=c, pos=0, unit="4학년", who="4학년", group="g4")
        for m in (self.t4, self.t4b):
            self.assertIn(f"/c/{c.link_token}/u/{r.key}", self.page(m, "week"), m.name)
        other = self.member("육일", "6-1", "g6")
        for mode in MODES:
            html = self.page(other, mode)
            self.assertIn("사학년 칸", grid_of(html), mode)
            for s in (c.link_token, r.key, c.key):
                self.assertNotIn(s, html, mode)

    def test_manager_gets_manage_link_every_mode(self):
        for mode in MODES:
            html = self.page(self.boss, mode)
            self.assertIn(f'href="/s/{self.hub.token}/c/{self.c.key}/"', html, mode)
            self.assertNotIn(self.c.link_token, html, mode)


class DutyEdgeTests(Base):
    """기준 7의 경계: 과거 31일, 이름 일부·없는 유형, 다른 공간, 로그."""

    def add(self, m, line, d=TODAY.isoformat(), client=None, base=None):
        c = client or self.as_(m)
        return c.post(f"{base or self.base}/today/duty", {"view": "week", "mode": "week", "w": "2026-10-26", "tab": "school",
                                                         "line": line, "date": d, "slot": ""}, headers=HX)

    def test_past_boundary(self):
        self.add(self.t4, "오일 출장", d=(TODAY - timedelta(days=31)).isoformat())
        self.assertEqual(Attendance.objects.count(), 1)
        r = self.add(self.t4, "오일 출장", d=(TODAY - timedelta(days=32)).isoformat())
        self.assertContains(r, "31일 안에만")
        self.assertNotContains(r, "오일 출장")
        self.assertEqual(Attendance.objects.count(), 1)

    def test_partial_name_and_unknown_kind_rejected(self):
        for line in ("사 출장", "사일이 출장", "오 일 이 출장", "오일 병원", "오일 출장중", "오일 회의"):
            r = self.add(self.t4, line)
            self.assertEqual(r["HX-Retarget"], "#cellErr", line)
        self.assertFalse(Attendance.objects.exists())

    def test_reason_in_parentheses_not_stored(self):
        self.add(self.t4, "오일 조퇴(병원진료) 14:00")
        a = Attendance.objects.get()
        self.assertEqual((a.kind, a.time_text), ("조퇴", "14:00~"))
        self.assertNotIn("병원", " ".join(str(v) for v in Attendance.objects.values_list().get()))

    def test_long_line_does_not_crash_or_echo(self):
        r = self.add(self.t4, "오일 출장 " + "비밀사유" * 200)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "비밀사유")

    def test_outsider_of_other_school_404(self):
        s = School.objects.create(neis_atpt="J10", neis_code="7538200", name="남초", plan="school", paid_until=date(2099, 1, 1))
        far = Space.objects.create(school=s, kind="school", grade=0, name="남초")
        fm = Member.objects.create(space=far, user=User.objects.create(username="google:남"), name="남", cls="4-1", is_manager=True)
        c = Client()
        c.force_login(fm.user)
        self.assertEqual(self.add(fm, "오일 출장", client=c).status_code, 404)
        mine = Attendance.objects.create(space=self.hub, date=TODAY, name="오일", kind="출장", by=self.t5)
        self.assertEqual(c.post(f"{self.base}/today/duty/{mine.key}/delete").status_code, 404)
        self.assertEqual(c.post(f"/s/{far.token}/today/duty/{mine.key}/delete").status_code, 404)  # 남의 공간 주소 + 우리 key
        self.assertTrue(Attendance.objects.filter(pk=mine.pk).exists())
        self.assertFalse(Attendance.objects.filter(space=far).exists())

    def test_anonymous_cannot_add_or_delete(self):
        a = Attendance.objects.create(space=self.hub, date=TODAY, name="오일", kind="출장", by=self.t5)
        c = Client()
        self.assertIn(c.post(f"{self.base}/today/duty", {"line": "오일 출장", "date": TODAY.isoformat()}).status_code, (302, 403, 404))
        self.assertIn(c.post(f"{self.base}/today/duty/{a.key}/delete").status_code, (302, 403, 404))
        self.assertEqual(Attendance.objects.count(), 1)

    def test_logs_have_no_reason_or_key(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeErrorFormatter())
        for name in ("", "django.request", "django.security"):
            logging.getLogger(name).addHandler(handler)
            self.addCleanup(logging.getLogger(name).removeHandler, handler)
        self.add(self.t4, "오일 조퇴 14:00 비밀사유병원")
        self.add(self.t4, "모르는사람 출장 비밀사유병원")
        self.add(self.t4, "오일 출장 비밀사유병원", d="2026-02-30")  # 400
        a = Attendance.objects.get()
        self.as_(self.t4b).post(f"{self.base}/today/duty/{a.key}/delete")  # 403(오일은 당사자라 지울 수 있다, 후속 수정 1)
        # 4xx는 설정상 남지 않는다(django.request ERROR). 500일 때 사유가 든 예외·지우기 주소가 로그에 남는지 본다.
        c = Client(raise_request_exception=False)
        c.force_login(self.t4.user)
        with mock.patch("today.schedule.read_line", side_effect=RuntimeError("비밀사유병원")):
            r = c.post(f"{self.base}/today/duty", {"view": "week", "line": "오일 출장 비밀사유병원", "date": TODAY.isoformat()})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("비밀사유", r.content.decode())
        with mock.patch("today.schedule.Attendance.delete", side_effect=RuntimeError("x")):
            self.assertEqual(c.post(f"{self.base}/today/duty/{a.key}/delete").status_code, 500)
        log = stream.getvalue()
        self.assertIn("/today/duty", log)  # 500 로그가 실제로 찍혔는지
        for s in ("비밀사유", "병원", "모르는사람", a.key, self.hub.token):
            self.assertNotIn(s, log)


class PersonalTraceTests(Base):
    """기준 8의 경계: 블록 칸 추가 토스트, 관리자의 그룹·개인 탭."""

    def setUp(self):
        super().setUp()
        self.secret = self.ev(self.t4, "비밀상담", ME, "b2", place="과학실", target="비밀대상")

    def clean(self, html, where):
        for s in ("비밀상담", "비밀대상", self.secret.key, "겹쳐요", "장소 겹침", "다른 일정"):
            self.assertNotIn(s, html, where)

    def test_block_cell_add_toast_and_reply(self):
        for m in (self.boss, self.t4b):
            for tab, scope in (("school", ALL), ("group", m.group), ("me", ME)):
                r = self.as_(m).post(f"{self.base}/today/events", {"view": "week", "mode": "day", "w": TODAY.isoformat(), "tab": tab,
                                                                  "line": "과학실 회의", "date": TODAY.isoformat(), "slot": "b2", "scope": scope},
                                     headers=HX)
                self.assertEqual(r.status_code, 200, (m.name, tab))
                self.clean(r.content.decode(), (m.name, tab))
                Event.objects.filter(by=m).delete()  # 다음 칸에서 내 일정끼리 겹치는 것은 정상이라 지운다

    def test_manager_every_tab_and_mode(self):
        for tab in ("school", "group", "me"):
            for mode in MODES:
                self.clean(self.page(self.boss, mode, tab=tab), (tab, mode))


class SameBlockEdgeTests(Base):
    """기준 10: 일간 학교 탭과 그룹·개인 탭에서도 블록 이름이 한 번만."""

    def test_every_tab_and_mode(self):
        for tab, scope in (("school", ALL), ("group", "g4"), ("me", ME)):
            e = self.ev(self.t4, f"2블록 안전교육 {tab}", scope, "b2")
            for mode in MODES:
                self.assertLessEqual(card(self.page(self.t4, mode, tab=tab), e).count("2블록"), 1, (tab, mode))


class QueryTabTests(Base):
    """기준 13: 개인 탭과 관리자 그룹 탭도 건수와 무관."""

    def count(self, m, tab, mode):
        with CaptureQueriesContext(connection) as q:
            self.page(m, mode, tab=tab)
        return len(q)

    def test_flat(self):
        self.ev(self.t4, "처음 개인", ME, "b1", place="강당")
        self.ev(self.boss, "처음 교무", "office", "b1", place="강당")
        keys = [(self.t4, "me", m) for m in MODES] + [(self.boss, "group", m) for m in MODES] + [(self.boss, "me", m) for m in MODES]
        before = {k: self.count(*k) for k in keys}
        for i in range(8):
            self.ev(self.t4, f"개인{i}", ME, "b1", place="강당", changed_from=[["장소", "옛", "강당"]])
            self.ev(self.boss, f"교무{i}", "office", "b2", place="음악실")
            Attendance.objects.create(space=self.hub, date=TODAY, name="오일", kind="출장", by=self.t5)
        for k, n in before.items():
            self.assertEqual(self.count(*k), n, (k[0].name, k[1], k[2]))
