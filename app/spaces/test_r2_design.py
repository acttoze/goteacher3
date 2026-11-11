"""33번 R2 디자인 점검 [출시 전 고침]의 화면 약속. 측정(높이·위치)은 헤드리스로 확인했고, 여기서는 서버가 내는 값만 본다.

- 동학년 공간은 문구·순서가 그대로다(NEIS 표시, 일과 먼저, 더 보기 없음).
- 견적서는 값이 없으면 빈칸이고 지어낸 값이 없다. 값이 있으면 채운다.
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from notices.models import Notice, NoticeVersion, audience_short, version_label
from repairs.models import Repair
from today.models import Event

from .models import Member, PlanRequest, School, Space

User = get_user_model()
KEYS = dict(SCHOOL_PLAN_PRICE="", SUPPLIER_NAME="", SUPPLIER_BIZ_NO="", SUPPLIER_ADDRESS="", SUPPLIER_ACCOUNT="", SUPPLIER_CEO="",
            SCHOOL_PLAN_VAT="", SCHOOL_PLAN_TOTAL="", QUOTE_VALID_DAYS="")


def login(user):
    c = Client()
    c.force_login(user)
    return c


class Base(TestCase):
    def setUp(self):
        self.school = School.objects.create(neis_atpt="J10", neis_code="1", name="새솔초", plan="school", paid_until=date(2030, 1, 1))
        self.hub = Space.objects.create(school=self.school, kind="school", grade=0, name="새솔초")
        self.boss = self.member(self.hub, "boss", "교감쌤", "교무실", "office", is_manager=True)
        self.t = self.member(self.hub, "t", "담임쌤", "4-1", "g4")
        self.base = f"/s/{self.hub.token}"

    def member(self, space, sub, name, cls, group="", **kw):
        u = User.objects.create(username=f"google:{sub}", first_name=name, email=f"{sub}@example.org")
        return Member.objects.create(space=space, user=u, name=name, cls=cls, group=group, **kw)

    def notice(self, title="공지", must=True, audience="all", v=1):
        n = Notice.objects.create(space=self.hub, by=self.boss, by_name="교감쌤", title=title, must=must, audience=audience)
        for i in range(1, v + 1):
            NoticeVersion.objects.create(notice=n, v=i, fields={"제목": title}, body="", by="교감쌤")
        return n


class TodayTests(Base):
    def test_must_notice_button_and_todo_above_day(self):
        n = self.notice()
        body = login(self.t.user).get(f"{self.base}/today").content.decode()
        self.assertIn(f'<a class="btn pri" href="{self.base}/board/{n.key}/">공지 확인하기</a>', body)
        self.assertLess(body.index('data-open="#dlgTodo"'), body.index('id="dayH"'))  # 숫자 단추는 일과 위(2026-11-11)

    def test_count_buttons_above_week_open_lists_with_more(self):
        """2026-11-11: 바뀐 것·낼 것·걷는 것은 일과 위 숫자 단추, 누르면 목록 창, 창 아래 더보기는 그 페이지로."""
        body = login(self.t.user).get(f"{self.base}/today").content.decode()
        self.assertIn("오늘 마감 없음", body)
        for dlg, more in (("dlgFeed", f"{self.base}/schedule"), ("dlgTodo", f"{self.base}/c/?tab=todo"), ("dlgSent", f"{self.base}/c/?tab=sent")):
            self.assertLess(body.index(f'data-open="#{dlg}"'), body.index('id="dayH"'))
            box = body[body.index(f'id="{dlg}"'):]
            box = box[:box.index('class="scrim"') if 'class="scrim"' in box else len(box)]
            self.assertIn(f'<a class="btn sec" href="{more}">더보기</a>', box)

    def _events(self, space, by, n=6):
        for i in range(n):
            Event.objects.create(space=space, date=timezone.localdate(), title=f"일정{i}", scope="all", by=by)

    def test_feed_dialog_lists_all_with_count(self):
        """창 안에서는 4개로 자르지 않고 모두(2026-11-11). 단추 숫자 = 목록 수."""
        self._events(self.hub, self.boss)
        body = login(self.t.user).get(f"{self.base}/today").content.decode()
        self.assertEqual(body.split('id="feed"')[1].split("</ul>")[0].count("<li"), 6)
        self.assertRegex(body, r'data-open="#dlgFeed"[^>]*><span>[^<]*</span><b class="num">6</b>')

    def test_grade_space_is_unchanged(self):
        grade = Space.objects.create(school=self.school, grade=4, name="4학년")
        m = self.member(grade, "g", "동학년쌤", "4-1")
        self._events(grade, m)
        body = login(m.user).get(f"/s/{grade.token}/today").content.decode()
        self.assertEqual(body.split('id="feed"')[1].split("</ul>")[0].count("<li"), 6)
        self.assertLess(body.index('data-open="#dlgTodo"'), body.index('id="dayH"'))


class SchoolTests(Base):
    def test_empty_school_steps_and_one_line(self):
        Member.objects.filter(pk=self.t.pk).delete()
        body = login(self.boss.user).get(f"{self.base}/school").content.decode()
        for word in ("초대 링크 보내기", "업무분장 붙여넣기", "담당자 지정", "아직 없는 소속: 1학년·2학년"):
            self.assertIn(word, body)
        self.assertNotIn("아직 아무도 없어요", body)
        self.assertNotIn("학교 업무 요약", body)
        self.assertEqual(body.count('class="card gcard"'), 1)

    def test_expired_banner_links_renew_and_invite_is_quiet(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        body = login(self.boss.user).get(f"{self.base}/school").content.decode()
        self.assertIn('href="/school-plan">학교 플랜 연장</a>', body)
        self.assertIn('<button class="btn sec" type="button" data-open="#inviteDlg">초대 링크 재발급</button>', body)
        self.assertNotIn("선생님 초대하기</button>", body)
        self.assertNotIn("초대 링크 보기", body)


class RepairTests(Base):
    def test_received_is_plain_and_urgent_comes_first(self):
        old = Repair.objects.create(space=self.hub, cat="info", title="급한것", urgent=True, by=self.t, by_name="담임쌤")
        Repair.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        Repair.objects.create(space=self.hub, cat="info", title="새것", by=self.t, by_name="담임쌤")
        body = login(self.boss.user).get(f"{self.base}/it").content.decode()
        self.assertLess(body.index("급한것"), body.index("새것"))
        self.assertEqual(Repair(status=Repair.Status.RECEIVED).status_pill, "")


class NoticeTests(Base):
    def test_labels(self):
        self.assertEqual((version_label(1), version_label(3)), ("처음 글", "3차 수정"))
        self.assertEqual(audience_short(",g1,g2,g3,g4,g5,g6,office,"), "전 학년·교무실")
        self.assertEqual(audience_short(",g4,"), "4학년")
        self.assertEqual(audience_short("all"), "전체")

    def test_plain_words_and_ack_in_side_card(self):
        n = self.notice(v=2)
        c = login(self.t.user)
        for path in ("/board", f"/board/{n.key}/", f"/board/{n.key}/?v=1"):
            body = c.get(self.base + path).content.decode()
            main = re.sub(r'(?:value|href|action)="[^"]*"', "", body[body.index("<main"):])  # 토큰 글자에 우연히 든 낱말 제외
            for word in ("v2", "판 기록", "예전 판", "최신 판", "실질 변경"):
                self.assertNotIn(word, main, (path, word))
        body = c.get(f"{self.base}/board/{n.key}/").content.decode()
        self.assertEqual(body.count(">확인했어요</button>"), 1)
        self.assertGreater(body.index(">확인했어요</button>"), body.index("<aside"))
        self.assertIn('class="card ackbox"', body)

    def test_forms_use_label_file_button(self):
        for path in ("/board/new", "/repairs/new"):
            r = login(self.boss.user).get(self.base + path)
            self.assertContains(r, '<label class="btn sec sm">')
            self.assertContains(r, "data-names=")
            self.assertNotContains(r, "/</p>")


class QuoteTests(Base):
    def req(self):
        return PlanRequest.objects.create(school=self.school, user=self.t.user, by_name="담임쌤", position="교무부장", email="a@example.org")

    @override_settings(**KEYS)
    def test_blank_cells_and_structure_without_settings(self):
        r = login(self.t.user).get(f"/school-plan/q/{self.req().key}")
        for word in ("문서 번호", "발행일", "유효 기간", "대표자", "부가세", "합계 금액", "(인)", "GT-"):
            self.assertContains(r, word)
        body = r.content.decode()
        self.assertNotIn("포함)", body)  # 부가세 처리 방식을 지어내지 않는다
        self.assertNotRegex(body, r"\d[\d,]*\s*원")
        self.assertIn('<td class="amt num"></td>', body)

    @override_settings(SCHOOL_PLAN_PRICE="390000", SUPPLIER_CEO="대표표시", SCHOOL_PLAN_VAT="부가세표시", SCHOOL_PLAN_TOTAL="429000", QUOTE_VALID_DAYS="30",
                       SUPPLIER_NAME="상호표시", SUPPLIER_BIZ_NO="번호표시", SUPPLIER_ADDRESS="주소표시", SUPPLIER_ACCOUNT="계좌표시")
    def test_values_fill_cells(self):
        q = self.req()
        PlanRequest.objects.filter(pk=q.pk).update(created_at=datetime(2026, 11, 4, 12, tzinfo=ZoneInfo("Asia/Seoul")))
        r = login(self.t.user).get(f"/school-plan/q/{q.key}")
        for word in ("390,000원", "대표표시", "부가세표시", "429,000원", "2026년 12월 4일까지", "GT-20261104-"):
            self.assertContains(r, word)
        self.assertNotContains(r, "빈칸은 담당자에게")
        self.assertNotContains(r, q.key)  # 주소 토큰이 문서 번호로 새지 않는다(되돌아가기 링크 없음)

    @override_settings(**KEYS)
    def test_pending_shows_quote_first_and_folds_form(self):
        u = User.objects.create(username="google:apply", first_name="신청쌤", email="apply@example.org")  # 아직 학교 공간이 없는 신청자
        q = PlanRequest.objects.create(school=self.school, user=u, by_name="신청쌤", position="교무부장", email="apply@example.org")
        body = login(u).get("/school-plan").content.decode()
        self.assertIn(f'<a class="btn pri" href="/school-plan/q/{q.key}">견적서 보기</a>', body)
        self.assertLess(body.index("신청했어요"), body.index('<details class="plan-again"'))
        self.assertLess(body.index('<details class="plan-again"'), body.index("신청하고 견적서 받기"))
