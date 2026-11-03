"""J1 독립 검증(gt-qa): test_attendance.py에 없는 경계만."""
import time
from datetime import date, datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.apps import apps
from django.contrib.sessions.models import Session
from django.core import signing
from django.test import Client, SimpleTestCase

from collect.paste import PasteError, html_to_tsv
from hwpfiles import retention

from .attendance import read_attendance
from .models import Attendance, Duty, Member, School
from .test_attendance import Web

MARK = "쿼크표식"
TODAY = date(2026, 11, 3)
KNOWN = {"송민재", "김도윤", "최수아"}

# 칸마다 표식을 넣은 표본: 나이스 형식, HTML, 칸 순서, 머리글 없음, 병합, 사유 왼쪽, 연가·이름 낱말, 머리글 낱말들
SAMPLES = {
    "나이스 TSV": {"paste": f"성명\t복무구분\t기간\t사유\n송민재\t출장\t09:00~17:00\t{MARK} 연수\n김도윤\t병가\t종일\t{MARK} 감기"},
    "나이스 전체 칸": {"paste": "부서\t직위\t성명\t근무상황\t시작일시\t종료일시\t일수\t사유\t행선지\t결재상태\t신청일\n"
                       f"교무\t교사\t송민재\t조퇴\t2026.11.03 14:00\t2026.11.03 16:40\t0일 2시간\t{MARK}\t{MARK}\t{MARK}\t2026.11.01"},
    "HTML": {"html": f"<table><tr><td>성명</td><td>구분</td><td>사유</td></tr><tr><td>송민재</td><td>공가</td><td><p>{MARK} 병원</p></td></tr></table>"},
    "칸 순서": {"paste": f"사유\t기간\t구분\t이름\n{MARK} 병원\t14:00~\t조퇴\t김도윤"},
    "머리글 없음": {"paste": f"교사\t송민재\t출장\t09:00~\t{MARK} 연수\n교사\t김도윤\t병가\t종일\t{MARK} 감기"},
    "병합": {"html": "<table><tr><th rowspan=2>성명</th><th rowspan=2>구분</th><th colspan=2>기간</th><th rowspan=2>사유</th></tr>"
                     f"<tr><th>시작</th><th>종료</th></tr><tr><td rowspan=2>송민재</td><td>외출</td><td>11:00</td><td>12:30</td><td rowspan=2>{MARK}</td></tr>"
                     "<tr><td>조퇴</td><td>15:00</td><td>16:40</td></tr></table>"},
    "사유 왼쪽": {"paste": f"{MARK} 입원\t송민재\t조퇴\t14:00~\n{MARK} 병원\t김도윤\t연가\t종일"},
    "사유에 연가·이름": {"paste": f"성명\t구분\t기간\t사유\n김도윤\t조퇴\t14:00~\t연가 대신 {MARK} 최수아 병문안\n최수아\t\t종일\t연가 {MARK}"},
    "머리글 낱말": {"paste": f"성명\t구분\t시간\t비고\t목적\t행선지\t사유및내용\t특이사항\t병명\n송민재\t출장\t09:00~\t{MARK}\t{MARK}\t{MARK}\t{MARK}\t{MARK}\t{MARK}"},
    "시간·유형 칸 안": {"paste": f"성명\t구분\t시간\n송민재\t조퇴\t14:00 {MARK}\n김도윤\t병가({MARK})\t종일\n최수아\t연가 {MARK}\t종일"},
}


class SampleTests(Web):
    def test_mark_nowhere_for_each_sample(self):
        """표본마다 미리보기·서명 값·저장 뒤 화면·DB 전 칼럼·세션을 검색한다(다음 표본이 덮어쓰기 전에)."""
        salt = f"attendance:{self.hub.pk}:{self.m4.pk}"
        for label, data in SAMPLES.items():
            with self.subTest(label):
                r = self.preview(**data)
                self.assertEqual(r.status_code, 200)
                self.assertTrue(r.context["rows"])
                self.assertNotIn(MARK, r.content.decode())
                payload = signing.loads(r.context["signed"], salt=salt)
                self.assertEqual(set(payload), {"rows", "skipped"})
                self.assertNotIn(MARK, str(payload))
                self.assertEqual(self.manager.post(self.url, {"rows": r.context["signed"]}).status_code, 302)
                for path in ("school", "school?tab=duty", "today?show=school", "schedule", "schedule/print", "school/attendance"):
                    self.assertNotIn(MARK, self.manager.get(f"{self.base}/{path}").content.decode(), path)
                for model in apps.get_models():
                    for row in model.objects.values_list():
                        self.assertNotIn(MARK, str(row), model.__name__)
                for s in Session.objects.all():
                    self.assertNotIn(MARK, str(s.get_decoded()))

    def test_error_responses_hold_no_mark(self):
        for text in (f"{MARK}\t{MARK}", f"성명\t사유\n송민재\t{MARK}", f"성명\t구분\n{MARK}{MARK}{MARK}{MARK}\t{MARK}", f"{MARK} 한 칸"):
            r = self.preview(paste=text)
            self.assertEqual(r.status_code, 200)
            self.assertNotIn(MARK, r.content.decode())

    def test_big_inputs(self):
        """500줄 왕복, 줄 수 초과, 글자 수 초과, 전역 상한. 표식은 어떤 응답에도 없다."""
        names = [f"가{chr(0xAC00 + i)}" for i in range(500)]
        Duty.objects.bulk_create(Duty(space=self.hub, dept="교무", task="가", who=n, order=i) for i, n in enumerate(names))
        rows = "\n".join(f"{n}\t연가\t종일\t{MARK}" for n in names)
        r = self.preview(paste="성명\t구분\t시간\t사유\n" + rows)
        self.assertEqual(len(r.context["rows"]), 500)
        self.assertNotIn(MARK, r.content.decode())
        self.assertEqual(self.manager.post(self.url, {"rows": r.context["signed"]}).status_code, 302)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 500)
        self.assertEqual(self.manager.get(f"{self.base}/school").status_code, 200)
        for text in ("성명\t구분\n" + "송민재\t연가\n" * 501, f"성명\t구분\t사유\n송민재\t연가\t{MARK * 80000}"):
            r = self.preview(paste=text)
            self.assertContains(r, "500줄")
            self.assertNotContains(r, MARK)
        r = self.manager.post(f"{self.url}/preview", {"html": "<table>" * 2 + MARK * 400000})
        self.assertNotIn(MARK, r.content.decode())

    def test_hostile_span_and_markup(self):
        html = ("<table><tr><th colspan=999999>성명</th><th rowspan=999999>구분</th></tr><tr><td><img src=x onerror=alert(1)>송민재</td></tr>"
                "<tr><td>김도윤<script>alert(1)</script></td><td>조퇴</td></tr></table>")
        r = self.preview(html=html)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "onerror")
        self.assertNotContains(r, "<script>alert")


class ReadBoundaryTests(SimpleTestCase):
    def read(self, text):
        return read_attendance(text, TODAY, KNOWN)

    def brief(self, text):
        return [(r["name"], r["kind"], r["time"]) for r in self.read(text)["rows"]]

    def test_nice_like_columns(self):
        d = self.read("연번\t부서\t직위\t성명\t근무상황\t시작일자\t시작시간\t종료일자\t종료시간\t사유\t결재상태\n"
                      "1\t교무\t교사\t김도윤\t조퇴\t2026-11-03\t14:00\t2026-11-03\t16:40\t병원\t결재완료")
        self.assertEqual(d["rows"], [{"name": "김도윤", "kind": "조퇴", "time": "14:00~16:40", "date": "2026-11-03"}])
        self.assertEqual(self.brief("성명\t구분\t기간\n김도윤\t출장\t2026. 11. 3.(화) 09:00 ~ 2026. 11. 3.(화) 17:00"), [("김도윤", "출장", "09:00~17:00")])
        self.assertEqual(self.brief("성명\t구분\t기간\n김도윤\t조퇴\t14:00:00~16:40:00"), [("김도윤", "조퇴", "14:00~16:40")])

    def test_decision_table_kinds(self):
        """병가·공가·특별휴가는 휴가로만. 사유가 붙은 유형 칸은 사유를 싣지 않고, 모르는 유형은 못 읽은 줄로 센다."""
        for raw in ("병가", "공가", "특별휴가", "병 가", "병가(공무상)", "특별휴가（경조사）", "병가(감기"):
            self.assertEqual(self.read(f"성명\t구분\n김도윤\t{raw}")["rows"][0]["kind"], "휴가", raw)
        d = self.read("성명\t구분\n김도윤\t연가\n최수아\t특별휴가-자녀입원\n송민재\t모르는유형")
        self.assertEqual(len(d["rows"]), 1)
        self.assertEqual(d["skipped"], 2)
        self.assertNotIn("자녀", str(d))

    def test_unread_count_with_filler_and_merge(self):
        d = self.read("성명\t구분\n\t조퇴\n김도윤\t모름\n\t조퇴\n\t\n…\t…\n소계\t1\n없음\t없음")
        self.assertEqual([r["name"] for r in d["rows"]], ["김도윤"])  # 모르는 유형 다음의 빈 이름 줄은 병합으로 읽는다
        self.assertEqual(d["skipped"], 3)  # 앞 사람이 없는 빈 이름 줄, 모르는 유형, '없음' 줄. 빈 줄·'…'·소계는 세지 않는다

    def test_reversed_period_and_impossible_time(self):
        self.assertEqual(self.read("성명\t구분\t기간\n김도윤\t출장\t11/5~11/3\n송민재\t출장\t11/3")["skipped"], 1)
        self.assertEqual(self.brief("성명\t구분\t시간\n김도윤\t조퇴\t25:00~26:00"), [("김도윤", "조퇴", "")])

    def test_big_cells_are_quick(self):
        for text in ("성명\t구분\n김도윤\t" + "(" * 299000, "성명\t구분\t시간\n김도윤\t연가\t" + "1 " * 149000,
                     "성명\t구분\t시간\n김도윤\t연가\t2026" + " " * 299000, "성명\t구분\t사유\n" + "김도윤\t연가\t가(\n" * 500):
            start = time.monotonic()
            try:
                self.read(text)
            except ValueError:
                pass
            self.assertLess(time.monotonic() - start, 2)

    # ---------------------------------------------------------------- 고친 결함
    def test_reason_in_name_cell_is_not_saved(self):
        for cell in ("병원 진료", "김도윤 병원", "항암치료", "flu leave"):
            with self.subTest(cell):
                self.assertEqual(self.read(f"성명\t구분\n{cell}\t병가\n송민재\t연가")["rows"][0]["name"], "송민재")

    def test_reason_in_name_cell_html(self):
        for html in ("<table><tr><th>성명</th><th>구분</th></tr><tr><td>김도윤<br>입원</td><td>조퇴</td></tr><tr><td>송민재</td><td>연가</td></tr></table>",
                     "<table><tr><th>성명</th><th>구분</th></tr><tr><td colspan=2>병가</td></tr><tr><td>송민재</td><td>연가</td></tr></table>"):
            with self.subTest(html):
                self.assertEqual([r["name"] for r in self.read(html_to_tsv(html))["rows"]], ["송민재"])

    def test_no_header_left_reason_kind_word_rejected(self):
        with self.assertRaises(PasteError):
            self.read("연가\t김도윤\t조퇴\t14:00~\n연가\t송민재\t출장\t종일")

    def test_no_header_left_reason_name_rejected(self):
        with self.assertRaises(PasteError):
            self.read("최수아\t김도윤\t조퇴\t14:00~")

    def test_afternoon_carries_to_end_time(self):
        self.assertEqual(self.brief("성명\t구분\t시간\n김도윤\t조퇴\t오후 2시~4시")[0][2], "14:00~16:00")

    def test_hours_are_not_a_date(self):
        self.assertEqual(self.read("성명\t구분\t시간\n김도윤\t조퇴\t1.5시간")["rows"][0]["date"], "2026-11-03")

    def test_month_day_with_dash(self):
        self.assertEqual(self.read("성명\t구분\t일자\n김도윤\t출장\t11-05")["rows"][0]["date"], "2026-11-05")

    def test_total_word_only_in_name_column_counts(self):
        d = self.read("성명\t구분\t사유\n김도윤\t조퇴\t계\n송민재\t연가\t")
        self.assertEqual(len(d["rows"]) + d["skipped"], 2)


class SignedTests(Web):
    def token(self):
        return self.preview().context["signed"]

    def rows(self):
        return Attendance.objects.in_space(self.hub).count()

    def test_tampered_value_rejected(self):
        token = self.token()
        head, sep, tail = token.partition(":")
        for bad in (head[:-1] + ("A" if head[-1] != "A" else "B") + sep + tail, token[:-1] + ("A" if token[-1] != "A" else "B"), token + "x",
                    signing.dumps({"rows": [{"name": "위조", "kind": "연가", "time": "", "date": "2026-11-03"}], "skipped": 0},
                                  salt=f"attendance:{self.hub.pk}:{self.m4.pk}", key="wrong-key")):
            self.assertEqual(self.manager.post(self.url, {"rows": bad}).status_code, 400)
        self.assertEqual(self.rows(), 0)

    def test_old_value_rejected_after_hour(self):
        token = self.token()
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 3601):
            r = self.manager.post(self.url, {"rows": token})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "미리보기가 오래됐어요", status_code=400)
        self.assertEqual(self.rows(), 0)
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 3500):
            self.assertEqual(self.manager.post(self.url, {"rows": token}).status_code, 302)

    def test_value_from_other_school_rejected(self):
        """다른 학교 관리자의 서명 값은 이 학교에서 못 쓰고, 이 학교 값을 다른 학교로 가져가도 못 쓴다."""
        token = self.token()
        far_school = School.objects.create(neis_atpt="B11", neis_code="7010002", name="셋째초", plan="school")
        far_hub = far_school.spaces.create(kind="school", grade=0, name="셋째초")
        Member.objects.create(space=far_hub, user=self.u4, name="송민재", cls="교무실", group="office", is_manager=True)
        self.assertEqual(self.manager.post(f"/s/{far_hub.token}/school/attendance", {"rows": token}).status_code, 400)
        self.assertEqual(Attendance.objects.in_space(far_hub).count(), 0)

    def test_value_dead_after_writer_removed(self):
        Member.objects.filter(pk=self.m5.pk).update(can_duty=True)
        token = self.preview(self.teacher).context["signed"]
        Member.objects.filter(pk=self.m5.pk).update(can_duty=False)
        self.assertEqual(self.teacher.post(self.url, {"rows": token}).status_code, 403)
        self.assertEqual(self.rows(), 0)

    def test_value_dead_after_member_out(self):
        Member.objects.filter(pk=self.m5.pk).update(can_duty=True)
        token = self.preview(self.teacher).context["signed"]
        self.assertEqual(self.manager.post(f"{self.base}/members/{self.m5.key}/out").status_code, 302)
        self.assertIn(self.teacher.post(self.url, {"rows": token}).status_code, (302, 403, 404))
        self.assertEqual(self.rows(), 0)


class GateTests(Web):
    def test_methods(self):
        self.assertEqual(self.manager.get(f"{self.url}/preview").status_code, 405)
        self.assertEqual(self.manager.get(f"{self.base}/school/members/{self.m5.key}/attendance").status_code, 405)

    def test_anonymous_cannot_reach(self):
        c = Client()
        for method, path in ((c.get, self.url), (c.post, f"{self.url}/preview"), (c.post, f"{self.url}/clear")):
            r = method(path)
            self.assertIn(r.status_code, (302, 403, 404), path)
            self.assertNotEqual(r.status_code, 200)

    def test_free_plan_is_read_only(self):
        School.objects.filter(pk=self.school.pk).update(plan="free")
        self.assertEqual(self.preview().status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/school/members/{self.m5.key}/attendance", {"on": "1"}).status_code, 403)
        self.assertEqual(self.manager.get(f"{self.base}/school").status_code, 200)

    def test_grade_space_of_a_real_member_404(self):
        """학교 플랜이 없는 동학년 공간의 진짜 회원도 복무 주소가 없다."""
        far = Client()
        far.force_login(self.far_user)
        for method, path in ((far.get, "school/attendance"), (far.post, "school/attendance"), (far.post, "school/attendance/preview"),
                             (far.post, "school/attendance/clear")):
            self.assertEqual(method(f"/s/{self.far.token}/{path}").status_code, 404, path)

    def test_token_swap_between_school_and_grade(self):
        far = Client()
        far.force_login(self.far_user)
        Attendance.objects.create(space=self.hub, date=self.today, name="최수아", kind="연가")
        for path in ("school", "today?show=school", "schedule", "schedule/print"):
            self.assertEqual(far.get(f"{self.base}/{path}").status_code, 404, path)

    def test_writer_cannot_designate_and_position_gives_no_power(self):
        Member.objects.filter(pk=self.m5.pk).update(can_duty=True, position="교감")
        self.assertEqual(self.teacher.post(f"{self.base}/school/members/{self.m5.key}/attendance", {"on": "0"}).status_code, 403)
        self.m5.refresh_from_db()
        self.assertTrue(self.m5.can_duty)
        Member.objects.filter(pk=self.m5.pk).update(can_duty=False, position="교무실무사")
        self.assertEqual(self.preview(self.teacher).status_code, 403)

    def test_clear_needs_valid_day_and_only_that_space(self):
        Attendance.objects.create(space=self.hub, date=self.today, name="최수아", kind="연가")
        self.assertEqual(self.manager.post(f"{self.url}/clear", {"date": "2026-02-30"}).status_code, 400)
        self.assertEqual(self.manager.post(f"{self.url}/clear", {"date": (self.today + timedelta(days=9)).isoformat()}).status_code, 302)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 1)

    def test_row_survives_member_leaving(self):
        Attendance.objects.create(space=self.hub, date=self.today, name="김도윤", member=self.m5, by=self.m5, kind="조퇴")
        self.manager.post(f"{self.base}/members/{self.m5.key}/out")
        a = Attendance.objects.get(space=self.hub)
        self.assertIsNone(a.member_id)
        self.assertEqual(self.manager.get(f"{self.base}/school").status_code, 200)


class PurgeBoundaryTests(Web):
    def test_seven_days_counted_in_korean_time(self):
        """UTC 15:30은 한국 시각으로 다음 날이다. 그 날짜 기준으로 8일 지난 것만 지운다."""
        now = datetime(2026, 11, 20, 15, 30, tzinfo=dt_tz.utc)  # 한국 11/21 00:30
        for d, name in ((date(2026, 11, 13), "8일전"), (date(2026, 11, 14), "7일전")):
            Attendance.objects.create(space=self.hub, date=d, name=name, kind="연가")
        Attendance.objects.create(space=self.far, date=date(2026, 11, 13), name="먼곳8일전", kind="연가")
        retention.purge(now)
        self.assertEqual(set(Attendance.objects.values_list("name", flat=True)), {"7일전"})
