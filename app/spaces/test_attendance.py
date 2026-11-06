"""J1 복무: 나이스 복무현황 붙여넣기, 오늘 복무 현황, 일정·인쇄 표시, 7일 뒤 삭제.

불변식
- 사유 낱말은 미리보기 응답·저장 뒤 모든 응답·DB 전 칼럼·세션·로그 어디에도 없다. 500이 나도 없다.
- 유형은 유형 칸에서만, 병가·공가·특별휴가는 '휴가'로만.
- 쓰기는 관리자와 지정 입력자, 지정은 관리자만. 만료 학교는 읽기만. 동학년·다른 학교는 404.
- 동명이인·명부에 없는 이름은 글자로 저장하고 소속은 비운다. member는 권한에 쓰지 않는다.
"""
import io
import logging
from datetime import date, datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.apps import apps
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.management import call_command
from django.test import Client, SimpleTestCase
from django.utils import timezone

from collect.paste import PasteError, html_to_tsv
from config.logsafe import SafeErrorFormatter
from hwpfiles import retention

from .attendance import read_attendance
from .models import Attendance, Duty, Member, MemberLog
from .test_school_page import Base

TODAY = date(2026, 11, 3)
KNOWN = {"송민재", "김도윤", "정하은", "최수아", "노태윤", "오다은"}
NOW = datetime(2026, 11, 3, 11, 5, tzinfo=dt_tz(timedelta(hours=9)))  # 화요일로 고정: 주말에 돌려도 주간판·인쇄에 나온다
MARKS = ["병원 진료", "자녀 입원", "개인 용무", "교육지원청 연수", "감기"]
MARKS += [w.replace(" ", "") for w in MARKS]
NICE = ("성명\t복무구분\t기간\t사유\n"
        "송민재\t출장\t09:00~17:00\t교육지원청 연수\n"
        "김도윤\t조퇴\t14:00~16:40\t병원 진료\n"
        "정하은\t외출\t11:00~12:30\t개인 용무\n"
        "최수아\t연가\t종일\t\n"
        "노태윤\t병가\t종일\t감기\n"
        "오다은\t특별휴가\t종일\t자녀 입원")
HTML = ("<table><tr><td colspan='5'>일일 복무현황</td></tr>"
        "<tr><td>성 명</td><td>복무구분</td><td>기간</td><td>사유</td><td>비고</td></tr>"
        "<tr><td><p>송민재</p></td><td>출장</td><td>09:00~17:00</td><td>교육지원청 연수</td><td></td></tr>"
        "<tr><td>김도윤</td><td>공가</td><td>종일</td><td>병원 진료</td><td>자녀 입원</td></tr></table>")
MERGED = ("<table><tr><th rowspan='2'>성명</th><th rowspan='2'>구분</th><th colspan='2'>기간</th><th rowspan='2'>사유</th></tr>"
          "<tr><th>시작</th><th>종료</th></tr>"
          "<tr><td rowspan='2'>송민재</td><td>외출</td><td>11:00</td><td>12:30</td><td rowspan='2'>병원 진료</td></tr>"
          "<tr><td>조퇴</td><td>15:00</td><td>16:40</td></tr>"
          "<tr><td>김도윤</td><td>병가</td><td>종일</td><td>종일</td><td>자녀 입원</td></tr></table>")


def brief(rows):
    return [(r["name"], r["kind"], r["time"]) for r in rows]


class ReadTests(SimpleTestCase):
    def read(self, text):
        return read_attendance(text, TODAY, KNOWN)

    def test_nice_tsv(self):
        d = self.read(NICE)
        self.assertEqual(brief(d["rows"]), [("송민재", "출장", "09:00~17:00"), ("김도윤", "조퇴", "14:00~16:40"),
                                            ("정하은", "외출", "11:00~12:30"), ("최수아", "연가", "종일"),
                                            ("노태윤", "휴가", "종일"), ("오다은", "휴가", "종일")])
        self.assertEqual(d["skipped"], 0)
        self.assertEqual({r["date"] for r in d["rows"]}, {TODAY.isoformat()})

    def test_html_table(self):
        d = self.read(html_to_tsv(HTML))
        self.assertEqual(brief(d["rows"]), [("송민재", "출장", "09:00~17:00"), ("김도윤", "휴가", "종일")])

    def test_columns_in_other_order(self):
        d = self.read("사유\t기간\t직위\t구분\t이름\n병원 진료\t14:00~\t교사\t조퇴\t김도윤\n연가\t종일\t교사\t출장\t송민재")
        self.assertEqual(brief(d["rows"]), [("김도윤", "조퇴", "14:00~"), ("송민재", "출장", "종일")])

    def test_no_header(self):
        d = self.read("교사\t송민재\t출장\t09:00~17:00\t교육지원청 연수\n교사\t김도윤\t조퇴\t14:00~\t병원 진료\n교사\t최수아\t병가\t종일\t연가")
        self.assertEqual(brief(d["rows"]), [("송민재", "출장", "09:00~17:00"), ("김도윤", "조퇴", "14:00~"), ("최수아", "휴가", "종일")])

    def test_no_header_reason_left_of_kind(self):
        """사유 칸이 유형 칸 왼쪽이어도 명부 이름 칸만 읽는다."""
        d = self.read("자녀 입원\t송민재\t조퇴\t14:00~\n병원 진료\t김도윤\t연가\t종일")
        self.assertEqual(brief(d["rows"]), [("송민재", "조퇴", "14:00~"), ("김도윤", "연가", "종일")])
        self.assertNotIn("자녀", str(d))

    def test_no_header_unknown_name_counted_not_shown(self):
        d = self.read("송민재\t출장\t종일\n자녀 입원\t연가\t종일\n없는분\t연가\t종일\n\t외출\t11:00~")
        self.assertEqual(brief(d["rows"]), [("송민재", "출장", "종일")])
        self.assertEqual((d["skipped"], d["unknown"]), (3, 2))
        self.assertNotIn("자녀", str(d))
        self.assertNotIn("없는분", str(d))

    def test_no_header_without_roster_name_is_error(self):
        for text in ("자녀 입원\t연가\t종일", "연가\t자녀 입원\t종일\n연가\t병원 진료\t종일"):
            with self.assertRaises(PasteError) as e:
                self.read(text)
            self.assertNotIn("자녀", str(e.exception))

    def test_name_must_match_roster_with_or_without_header(self):
        d = self.read("성명\t구분\n없는분\t연가\n병원 진료 받음\t조퇴\n자녀 입원\t조퇴\n김 도 윤\t연가\n최수아 교사\t외출\n송민재선생님\t출장")
        self.assertEqual([r["name"] for r in d["rows"]], ["김도윤", "최수아", "송민재"])
        self.assertEqual((d["skipped"], d["unknown"]), (3, 3))
        self.assertNotIn("자녀", str(d))

    def test_latin_name_kept_readable(self):
        self.assertEqual(read_attendance("성명\t구분\nSarah Johnson\t연가", TODAY, {"SarahJohnson"})["rows"][0]["name"], "Sarah Johnson")

    def test_seven_security_inputs(self):
        """칸 밀림, 쪽이 바뀌며 순서 변경, 검색 조건 줄, HTML 세로 병합, 라틴 문자 사유, 이름 뒤 사유, 이름 칸에 사유만."""
        ok = "송민재\t연가\n"
        shifted = read_attendance("성명\t구분\t사유\n자녀입원\t김도윤\t조퇴\n" + ok, TODAY, KNOWN)
        self.assertEqual([r["name"] for r in shifted["rows"]], ["송민재"])
        with self.assertRaises(PasteError) as e:
            self.read("성명\t구분\t사유\n" + ok + "사유\t구분\t성명\n자녀입원\t연가\t김도윤")
        self.assertIn("머리글", str(e.exception))
        for search in ("검색조건\t자녀입원\t기간\t2026-10-01~2026-11-30\n성명\t구분\n" + ok,
                       "성명\t자녀입원\t구분\t연가\n성명\t구분\n" + ok):
            try:
                d = self.read(search)
            except PasteError as e:
                self.assertNotIn("자녀", str(e))
            else:
                self.assertEqual(d["rows"], [{"name": "송민재", "kind": "연가", "time": "종일", "date": "2026-11-03"}])
        merged = html_to_tsv("<table><tr><th>성명</th><th>구분</th></tr><tr><td rowspan=2>자녀입원</td><td>조퇴</td></tr><tr><td>연가</td></tr>"
                             "<tr><td>송민재</td><td>연가</td></tr></table>")
        self.assertEqual([r["name"] for r in self.read(merged)["rows"]], ["송민재"])
        for cell in ("flu leave", "김도윤 병원", "김도윤 자녀입원", "김도윤(병원)", "자녀입원", "자녀 입원"):
            d = self.read(f"성명\t구분\n{cell}\t조퇴\n{ok}")
            self.assertEqual([r["name"] for r in d["rows"]], ["송민재"], cell)
            self.assertNotIn(cell, str(d))

    def test_head_with_other_order_rejected_same_order_passes(self):
        self.assertEqual(len(self.read("성명\t구분\n송민재\t연가\n성명\t구분\n김도윤\t연가")["rows"]), 2)
        with self.assertRaises(PasteError):
            self.read("성명\t구분\n송민재\t연가\n구분\t성명\n연가\t김도윤")

    def test_no_header_ties_rejected(self):
        for text in ("연가\t김도윤\t조퇴\t14:00~", "최수아\t김도윤\t조퇴\t14:00~"):
            with self.assertRaisesMessage(PasteError, "머리글"):
                self.read(text)

    def test_family_or_health_kinds_become_absence(self):
        for raw in ("육아시간", "모성보호시간", "유연근무", "육아휴직", "질병휴직", "가족돌봄휴가"):
            self.assertEqual(self.read(f"성명\t구분\t시간\n김도윤\t{raw}\t15:00~16:00")["rows"][0], {"name": "김도윤", "kind": "부재", "time": "15:00~16:00", "date": "2026-11-03"}, raw)
        self.assertEqual(self.read("성명\t구분\n김도윤\t육아시간")["rows"][0]["time"], "")

    def test_time_with_half_day_carry(self):
        for raw, want in (("오후 2시~4시", "14:00~16:00"), ("오후 2:00~4:00", "14:00~16:00"), ("오전 9시~오후 1시", "09:00~13:00"), ("오전 11시~1시", "11:00~13:00")):
            self.assertEqual(self.read(f"성명\t구분\t시간\n김도윤\t조퇴\t{raw}")["rows"][0]["time"], want, raw)

    def test_hours_are_not_dates(self):
        for raw in ("1.5시간", "1.5"):
            self.assertEqual(self.read(f"성명\t구분\t시간\n김도윤\t조퇴\t{raw}")["rows"][0]["date"], "2026-11-03", raw)

    def test_more_date_forms(self):
        for raw in ("11-05", "26.11.05", "26-11-05", "2026.11.05", "11월 5일", "11/5"):
            self.assertEqual(self.read(f"성명\t구분\t일자\n김도윤\t출장\t{raw}")["rows"][0]["date"], "2026-11-05", raw)

    def test_grade_class_is_not_a_date(self):
        d = self.read("4-1\t김도윤\t출장\t종일\n4-2\t송민재\t연가\t종일")
        self.assertEqual(len(d["rows"]), 2)
        self.assertEqual(d["rows"][0]["date"], "2026-11-03")

    def test_total_judged_by_name_column(self):
        d = self.read("성명\t구분\t사유\n김도윤\t조퇴\t계\n합계\t3\t\n소계\t\t")
        self.assertEqual(([r["name"] for r in d["rows"]], d["skipped"], d["totals"]), (["김도윤"], 0, 2))

    def test_dates_beyond_31_days_skipped(self):
        d = self.read("성명\t구분\t일자\n김도윤\t출장\t2026-12-05\n송민재\t출장\t2026-12-04\n최수아\t출장\t2026-10-02\n정하은\t출장\t2026-10-03")
        self.assertEqual([r["name"] for r in d["rows"]], ["송민재", "정하은"])
        self.assertEqual(d["skipped"], 2)

    def test_search_range_is_not_the_day(self):
        self.assertEqual(self.read("기간 2026-10-20~2026-11-30\t\n성명\t구분\n김도윤\t출장")["rows"][0]["date"], "2026-11-03")

    def test_too_many_columns_no_header(self):
        with self.assertRaisesMessage(PasteError, "500줄"):
            self.read("김도윤\t연가" + "\t가" * 61)

    def test_merged_cells(self):
        d = self.read(html_to_tsv(MERGED))
        self.assertEqual(brief(d["rows"]), [("송민재", "외출", "11:00~12:30"), ("송민재", "조퇴", "15:00~16:40"), ("김도윤", "휴가", "종일")])

    def test_excel_merged_name_is_blank_below(self):
        d = self.read("성명\t구분\t시간\n송민재\t외출\t11:00~12:30\n\t조퇴\t15:00~")
        self.assertEqual(brief(d["rows"])[1], ("송민재", "조퇴", "15:00~"))

    def test_kind_word_in_reason_is_not_read(self):
        d = self.read("성명\t구분\t기간\t사유\n송민재\t조퇴\t14:00~\t연가 대신 조퇴\n김도윤\t\t종일\t연가")
        self.assertEqual(brief(d["rows"]), [("송민재", "조퇴", "14:00~")])
        self.assertEqual(d["skipped"], 1)  # 유형 칸이 빈 줄은 사유 칸의 '연가'로 채우지 않는다

    def test_no_header_reason_with_kind_word_does_not_win(self):
        d = self.read("송민재\t조퇴\t14:00~\t연가\n김도윤\t출장\t09:00~\t교육\n최수아\t외출\t11:00~\t연가")
        self.assertEqual([r["kind"] for r in d["rows"]], ["조퇴", "출장", "외출"])

    def test_kinds_become_vacation_and_parens_dropped(self):
        for raw in ("병가", "공가", "특별휴가", "병가(병원 진료)", "경조사휴가", "휴가"):
            self.assertEqual(self.read(f"성명\t구분\n송민재\t{raw}")["rows"][0]["kind"], "휴가", raw)
        for raw, kind in (("연가", "연가"), ("관외출장", "출장"), ("지각", "지각"), ("병조퇴", "조퇴")):
            self.assertEqual(self.read(f"성명\t구분\n송민재\t{raw}")["rows"][0]["kind"], kind, raw)

    def test_unread_lines_counted_exactly(self):
        d = self.read("성명\t구분\t기간\n송민재\t출장\t종일\n김도윤\t모름\t종일\n3반 교무실 복무 담당자 홍길동\t연가\t종일\n\t\t\n합계\t\t\n"
                      "최수아\t연가\t13월 40일\n성명\t구분\t기간\n…\t…\t…")
        self.assertEqual(len(d["rows"]), 1)
        self.assertEqual(d["skipped"], 3)  # 모르는 유형, 이름 아닌 글자, 없는 날짜. 빈 줄·합계·다시 나온 머리글·'…'은 세지 않는다

    def test_reason_never_in_result(self):
        for text in (NICE, html_to_tsv(HTML), html_to_tsv(MERGED)):
            blob = str(read_attendance(text, TODAY, KNOWN))
            for w in MARKS:
                self.assertNotIn(w, blob)

    def test_name_with_reason_in_parens_not_read(self):
        d = self.read("성명\t구분\n김 도 윤(병원 진료)\t조퇴\n송민재\t연가")
        self.assertEqual(([r["name"] for r in d["rows"]], d["unknown"]), (["송민재"], 1))

    def test_time_cell_keeps_time_only(self):
        self.assertEqual(self.read("성명\t구분\t시간\n송민재\t조퇴\t14:00~ 병원 진료")["rows"][0]["time"], "14:00~")
        self.assertEqual(self.read("성명\t구분\t시간\n송민재\t조퇴\t병원 진료")["rows"][0]["time"], "")
        self.assertEqual(self.read("성명\t구분\t시간\n송민재\t조퇴\t오후 2시 30분")["rows"][0]["time"], "14:30~")
        self.assertEqual(self.read("성명\t구분\t시간\n송민재\t연가\t1일 0시간")["rows"][0]["time"], "종일")
        self.assertEqual(self.read("성명\t구분\t시간\n송민재\t연가\t오전")["rows"][0]["time"], "오전")

    def test_other_dates_are_ignored_columns(self):
        d = self.read("성명\t구분\t신청일자\t기간\n송민재\t출장\t2026-10-01\t2026-11-03 09:00 ~ 2026-11-03 17:00")
        self.assertEqual((d["rows"][0]["date"], d["rows"][0]["time"]), ("2026-11-03", "09:00~17:00"))

    def test_date_from_table(self):
        d = self.read("일자\t성명\t구분\n11/5(목)\t송민재\t출장")
        self.assertEqual(d["rows"][0]["date"], "2026-11-05")
        d = self.read("복무현황 2026.11.04.\t\t\n성명\t구분\t시간\n송민재\t출장\t종일")
        self.assertEqual(d["rows"][0]["date"], "2026-11-04")

    def test_period_shows_that_day_only(self):
        d = self.read("성명\t구분\t기간\n송민재\t출장\t2026-11-02 ~ 2026-11-04")
        self.assertEqual((d["rows"][0]["date"], d["rows"][0]["time"]), ("2026-11-03", "종일"))
        d = self.read("성명\t구분\t시작\t종료\n송민재\t출장\t11/3 13:00\t11/5 17:00")
        self.assertEqual((d["rows"][0]["date"], d["rows"][0]["time"]), ("2026-11-03", "13:00~"))
        d = self.read("성명\t구분\t기간\n송민재\t출장\t11/1 09:00~11/3 15:00")
        self.assertEqual((d["rows"][0]["date"], d["rows"][0]["time"]), ("2026-11-03", "~15:00"))

    def test_period_uses_day_of_table_not_today(self):
        d = self.read("성명\t구분\t기간\n송민재\t출장\t11/9\n김도윤\t연가\t11/9\n최수아\t출장\t11/8~11/10")
        self.assertEqual({r["date"] for r in d["rows"]}, {"2026-11-09"})

    def test_duplicates_collapsed(self):
        self.assertEqual(len(self.read("성명\t구분\n송민재\t출장\n송민재\t출장")["rows"]), 1)

    def test_errors_never_hold_values(self):
        for text in ("", "   \n", "성명\n송민재 병원 진료", "성명\t사유\n송민재\t병원 진료", "가\t나\t병원 진료\n다\t라\t병원 진료",
                     "성명\t구분\n합계\t"):
            with self.assertRaises(PasteError, msg=text) as e:
                self.read(text)
            self.assertNotIn("병원", str(e.exception))

    def test_too_big(self):
        with self.assertRaisesMessage(PasteError, "500줄"):
            self.read("성명\t구분\n" + "송민재\t출장\n" * 501)
        with self.assertRaisesMessage(PasteError, "500줄"):
            self.read("성명\t구분\t사유\n송민재\t출장\t" + "가" * 300_001)


class Web(Base):
    def setUp(self):
        for p in (mock.patch("spaces.neis.schedule", return_value=[]), mock.patch("django.utils.timezone.now", return_value=NOW)):
            p.start()
            self.addCleanup(p.stop)
        super().setUp()
        Duty.objects.create(space=self.hub, dept="교무", task="복무", who="최수아, 노태윤, 오다은", order=0)
        self.url = f"{self.base}/school/attendance"
        self.today = timezone.localdate()

    def preview(self, client=None, **data):
        return (client or self.manager).post(f"{self.url}/preview", data or {"paste": NICE})

    def save(self, client=None, **data):
        """미리보기가 준 서명 값으로 바꾸기."""
        r = self.preview(client, **data)
        return (client or self.manager).post(self.url, {"rows": r.context["signed"]})

    def assertClean(self, blob):
        for w in MARKS:
            self.assertNotIn(w, blob)


class NoReasonTests(Web):
    def test_preview_response_has_no_reason_or_raw(self):
        for data in ({"paste": NICE}, {"html": HTML}, {"html": MERGED}):
            r = self.preview(**data)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.context["rows"])
            self.assertClean(r.content.decode())
            self.assertNotIn("tsv", r.context)

    def test_signed_value_holds_parsed_fields_only(self):
        token = self.preview().context["signed"]
        payload = signing.loads(token, salt=f"attendance:{self.hub.pk}:{self.m4.pk}")
        self.assertClean(str(payload))
        self.assertEqual(set(payload["rows"][0]), {"name", "kind", "time", "date"})

    def test_saved_everywhere_clean(self):
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 6)
        for url in (f"{self.base}/school", f"{self.base}/school?tab=duty", f"{self.base}/today?show=school",
                    f"{self.base}/schedule", f"{self.base}/schedule/print", self.url):
            r = self.manager.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertClean(r.content.decode())
        for model in apps.get_models():
            for row in model.objects.values_list():
                self.assertClean(str(row))
        for s in Session.objects.all():
            self.assertClean(str(s.get_decoded()))

    def test_no_reason_field_on_model(self):
        self.assertEqual({f.name for f in Attendance._meta.get_fields()},
                         {"id", "key", "space", "date", "name", "member", "kind", "time_text", "by", "created_at"})

    def _capture(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeErrorFormatter())
        loggers = [logging.getLogger(), logging.getLogger("django.request"), logging.getLogger("django.security")]
        for x in loggers:
            x.addHandler(handler)
            self.addCleanup(x.removeHandler, handler)
        return stream

    def test_logs_clean_on_normal_flow_and_error(self):
        log = self._capture()
        self.save()
        self.preview(paste="성명\t사유\n송민재\t병원 진료")  # 못 읽는 표
        self.assertClean(log.getvalue())

    def test_500_leaves_no_reason(self):
        log = self._capture()
        c = Client(raise_request_exception=False)
        c.force_login(self.u4)
        with mock.patch("spaces.attendance.read_attendance", side_effect=RuntimeError(NICE)):
            r = c.post(f"{self.url}/preview", {"paste": NICE})
        self.assertEqual(r.status_code, 500)
        self.assertClean(r.content.decode())
        self.assertIn("Internal Server Error", log.getvalue())
        self.assertClean(log.getvalue())

    def test_save_without_valid_signature_rejected(self):
        before = Attendance.objects.count()
        for bad in ("", "x", signing.dumps({"rows": []}, salt="other")):
            r = self.manager.post(self.url, {"rows": bad})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(Attendance.objects.count(), before)

    def test_save_ignores_raw_fields(self):
        token = self.preview().context["signed"]
        self.manager.post(self.url, {"rows": token, "paste": NICE})
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 6)

    def test_signed_value_not_usable_by_other_writer(self):
        token = self.preview().context["signed"]
        Member.objects.filter(pk=self.m5.pk).update(can_duty=True)
        self.assertEqual(self.teacher.post(self.url, {"rows": token}).status_code, 400)


class NoHeaderWebTests(Web):
    TEXT = "자녀 입원\t송민재\t조퇴\t14:00~\n자녀 입원\t김도윤\t연가\t종일\n자녀 입원\t없는분\t연가\t종일"

    def test_marker_left_of_kind_nowhere(self):
        r = self.preview(paste=self.TEXT)
        self.assertEqual((r.context["skipped"], r.context["unknown"]), (0, 1))
        self.assertContains(r, "명부에 없는 이름")
        self.assertContains(r, "업무분장을 먼저")
        self.assertNotContains(r, "없는분")
        self.assertNotContains(r, "자녀 입원")
        self.assertEqual(self.manager.post(self.url, {"rows": r.context["signed"]}).status_code, 302)
        self.assertEqual(set(Attendance.objects.in_space(self.hub).values_list("name", flat=True)), {"송민재", "김도윤"})
        self.assertNotContains(self.manager.get(f"{self.base}/school"), "자녀 입원")
        for model in apps.get_models():
            for row in model.objects.values_list():
                self.assertNotIn("자녀 입원", str(row))


class ClearTests(Web):
    def setUp(self):
        super().setUp()
        self.other = self.today - timedelta(days=1)
        for d, n in ((self.today, "오늘분"), (self.today, "오늘둘"), (self.other, "어제분")):
            Attendance.objects.create(space=self.hub, date=d, name=n, kind="연가")
        self.clear = f"{self.url}/clear"

    def post(self, client=None, day=None, **headers):
        return (client or self.manager).post(self.clear, {"date": (day or self.today).isoformat()}, headers={"HX-Request": "true", **headers})

    def test_page_lists_days_with_confirm(self):
        page = self.manager.get(self.url)
        self.assertContains(page, "이 날 복무 지우기", count=2)
        self.assertContains(page, "hx-confirm=")

    def test_clears_that_date_only(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "복무 2줄을 지웠어요")
        self.assertEqual(set(Attendance.objects.in_space(self.hub).values_list("name", flat=True)), {"어제분"})

    def test_other_space_same_date_untouched(self):
        Attendance.objects.create(space=self.far, date=self.today, name="먼분", kind="연가")
        self.post()
        self.assertTrue(Attendance.objects.filter(space=self.far, name="먼분").exists())

    def test_writer_can_teacher_cannot(self):
        self.assertEqual(self.post(self.teacher).status_code, 403)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 3)
        Member.objects.filter(pk=self.m5.pk).update(can_duty=True)
        self.assertEqual(self.post(self.teacher).status_code, 200)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 1)

    def test_expired_forbidden(self):
        self.expire()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 3)
        self.assertNotContains(self.manager.get(self.url), "이 날 복무 지우기")

    def test_grade_and_other_school_404(self):
        far = Client()
        far.force_login(self.far_user)
        for token in (self.far.token, self.hub.token):
            self.assertEqual(far.post(f"/s/{token}/school/attendance/clear", {"date": self.today.isoformat()}).status_code, 404)

    def test_get_not_allowed_and_bad_date(self):
        self.assertEqual(self.manager.get(self.clear).status_code, 405)
        for bad in ("", "abc", "2026-13-40"):
            self.assertEqual(self.manager.post(self.clear, {"date": bad}).status_code, 400)
        self.assertEqual(Attendance.objects.in_space(self.hub).count(), 3)


class RosterTests(Web):
    def test_duty_only_name_read_without_member(self):
        self.save()
        a = Attendance.objects.get(space=self.hub, name="노태윤")
        self.assertIsNone(a.member_id)
        self.assertEqual(a.group_label, "")

    def test_duty_of_other_space_not_known(self):
        Duty.objects.create(space=self.far, dept="교무", task="가", who="먼분", order=0)
        r = self.preview(paste="성명\t구분\n먼분\t연가\n송민재\t연가")
        self.assertEqual(r.context["unknown"], 1)

    def test_all_unknown_is_error_with_count_only(self):
        r = self.preview(paste="성명\t구분\n자녀입원\t조퇴\n병원진료\t조퇴")
        self.assertContains(r, "명부에 없는 이름 2줄")
        self.assertNotContains(r, "자녀입원")
        self.assertFalse(r.context.get("rows"))


class ReplaceTests(Web):
    def test_replaces_that_date_only(self):
        other = self.today - timedelta(days=1)
        Attendance.objects.create(space=self.hub, date=other, name="어제분", kind="연가")
        Attendance.objects.create(space=self.hub, date=self.today, name="지울분", kind="연가")
        self.save()
        names = set(Attendance.objects.in_space(self.hub).values_list("name", flat=True))
        self.assertIn("어제분", names)
        self.assertNotIn("지울분", names)
        self.assertEqual(Attendance.objects.in_space(self.hub).filter(date=self.today).count(), 6)

    def test_by_and_member_matched(self):
        self.save()
        a = Attendance.objects.get(space=self.hub, name="김도윤")
        self.assertEqual(a.member_id, self.m5.pk)
        self.assertEqual(a.by_id, self.m4.pk)
        self.assertIsNone(Attendance.objects.get(space=self.hub, name="최수아").member_id)

    def test_same_name_or_unknown_kept_as_text_without_group(self):
        self.member("twin", "김도윤", "교무실", "office", "교무")
        r = self.preview()
        self.assertContains(r, "동명이인")
        self.assertNotContains(r, "명부에 없음")
        self.manager.post(self.url, {"rows": r.context["signed"]})
        a = Attendance.objects.get(space=self.hub, name="김도윤")
        self.assertIsNone(a.member_id)
        self.assertEqual(a.group_label, "")

    def test_name_match_gives_no_power(self):
        """이름이 같은 줄이 있어도 쓰기 권한은 생기지 않는다."""
        self.save()
        self.assertEqual(self.teacher.post(f"{self.url}/preview", {"paste": NICE}).status_code, 403)

    def test_unread_count_shown(self):
        r = self.preview(paste="성명\t구분\n송민재\t출장\n김도윤\t모름")
        self.assertContains(r, "읽지 못한 줄")
        self.assertEqual(r.context["skipped"], 1)

    def test_big_input_guidance(self):
        r = self.preview(paste="성명\t구분\n" + "송민재\t출장\n" * 501)
        self.assertContains(r, "500줄")
        self.assertFalse(r.context.get("rows"))

    def test_xss_escaped(self):
        html = ("<table><tr><td>성명</td><td>구분</td></tr><tr><td>&lt;script&gt;alert(1)&lt;/script&gt;</td><td>출장</td></tr>"
                "<tr><td>송민재</td><td>출장</td></tr></table>")
        self.assertNotContains(self.preview(html=html), "<script>alert")
        Attendance.objects.create(space=self.hub, date=self.today, name="<script>alert(1)</script>", kind="연가", time_text="<b>x")
        page = self.manager.get(f"{self.base}/school")
        self.assertNotContains(page, "<script>alert")
        self.assertNotContains(page, "<b>x")
        self.assertContains(page, "&lt;script&gt;alert")


class AccessTests(Web):
    def test_teacher_can_read_not_write(self):
        Attendance.objects.create(space=self.hub, date=self.today, name="최수아", kind="연가", time_text="종일")
        page = self.teacher.get(f"{self.base}/school")
        self.assertContains(page, "오늘 복무 현황")
        self.assertContains(page, "최수아")
        self.assertContains(page, "유형·시간만 보여요. 사유는 안 받아요")
        self.assertNotContains(page, "나이스 복무현황 붙여넣기")
        self.assertEqual(self.teacher.get(self.url).status_code, 403)
        self.assertEqual(self.teacher.post(f"{self.url}/preview", {"paste": NICE}).status_code, 403)
        self.assertEqual(self.teacher.post(self.url, {"rows": "x"}).status_code, 403)

    def test_designated_writer_can_write(self):
        self.assertEqual(self.manager.post(f"{self.base}/school/members/{self.m5.key}/attendance", {"on": "1"}).status_code, 302)
        self.m5.refresh_from_db()
        self.assertTrue(self.m5.can_duty)
        self.assertEqual(MemberLog.objects.in_space(self.hub).filter(name=self.m5.name).last().after, "복무 입력자")
        self.assertContains(self.teacher.get(f"{self.base}/school"), "나이스 복무현황 붙여넣기")
        self.assertEqual(self.save(self.teacher).status_code, 302)
        self.assertEqual(Attendance.objects.in_space(self.hub).first().by_id, self.m5.pk)
        self.assertEqual(self.manager.post(f"{self.base}/school/members/{self.m5.key}/attendance", {"on": "0"}).status_code, 302)
        self.assertEqual(self.teacher.get(self.url).status_code, 403)

    def test_only_manager_designates(self):
        Member.objects.filter(pk=self.m5.pk).update(can_duty=True)
        other = self.m4b
        r = self.teacher.post(f"{self.base}/school/members/{other.key}/attendance", {"on": "1"})
        self.assertEqual(r.status_code, 403)
        other.refresh_from_db()
        self.assertFalse(other.can_duty)

    def test_guest_not_designated(self):
        from accounts.models import new_guest
        g = Member.objects.create(space=self.hub, user=new_guest("손님"), name="손님", cls="교무실", group="office")
        r = self.manager.post(f"{self.base}/school/members/{g.key}/attendance", {"on": "1"})
        self.assertEqual(r.status_code, 400)
        g.refresh_from_db()
        self.assertFalse(g.can_duty)

    def test_position_gives_no_power(self):
        Member.objects.filter(pk=self.m5.pk).update(position="교무실무사", group="office")
        self.assertEqual(self.teacher.get(self.url).status_code, 403)

    def test_expired_read_ok_write_forbidden(self):
        Attendance.objects.create(space=self.hub, date=self.today, name="최수아", kind="연가")
        token = self.preview().context["signed"]
        self.expire()
        page = self.manager.get(f"{self.base}/school")
        self.assertContains(page, "최수아")
        self.assertNotContains(page, "나이스 복무현황 붙여넣기")
        self.assertEqual(self.manager.get(self.url).status_code, 200)
        self.assertEqual(self.manager.post(self.url, {"rows": token}).status_code, 403)
        self.assertEqual(self.manager.post(f"{self.url}/preview", {"paste": NICE}).status_code, 403)
        self.assertEqual(self.manager.post(f"{self.base}/school/members/{self.m5.key}/attendance", {"on": "1"}).status_code, 403)

    def test_grade_and_other_school_404(self):
        far = Client()
        far.force_login(self.far_user)
        for path in ("school/attendance", "school/attendance/preview", f"school/members/{self.m5.key}/attendance"):
            for token in (self.far.token, self.hub.token):
                r = far.post(f"/s/{token}/{path}", {"paste": NICE})
                self.assertEqual(r.status_code, 404, (path, token))
        self.assertEqual(far.get(f"/s/{self.far.token}/school/attendance").status_code, 404)

    def test_other_space_member_key_404(self):
        r = self.manager.post(f"{self.base}/school/members/{self.far_member.key}/attendance", {"on": "1"})
        self.assertEqual(r.status_code, 404)

    def test_query_isolated(self):
        Attendance.objects.create(space=self.far, date=self.today, name="먼분", kind="연가")
        self.assertNotContains(self.manager.get(f"{self.base}/school"), "먼분")
        self.assertNotContains(self.manager.get(f"{self.base}/today?show=school"), "먼분")
        self.assertNotContains(self.manager.get(f"{self.base}/schedule"), "먼분")
        self.assertNotContains(self.manager.get(f"{self.base}/schedule/print"), "먼분")


class ShownTests(Web):
    def setUp(self):
        super().setUp()
        Attendance.objects.create(space=self.hub, date=self.today, name="김도윤", member=self.m5, kind="조퇴", time_text="14:00~")

    def test_card_has_kind_name_group_time_and_count(self):
        page = self.teacher.get(f"{self.base}/school")
        html = page.content.decode()
        for w in ("조퇴", "김도윤", "14:00~", "5학년"):
            self.assertIn(w, html)
        self.assertEqual(page.context["n_attendance"], 1)
        self.assertLess(html.index("오늘 복무 현황"), html.index("학년분장 · 소속 현황"))

    def test_card_shows_today_only(self):
        Attendance.objects.create(space=self.hub, date=self.today - timedelta(days=1), name="어제분", kind="연가")
        self.assertNotContains(self.teacher.get(f"{self.base}/school"), "어제분")

    def test_count_is_people_not_rows(self):
        Attendance.objects.create(space=self.hub, date=self.today, name="김도윤", kind="외출")
        self.assertEqual(self.teacher.get(f"{self.base}/school").context["n_attendance"], 1)

    def test_today_school_wide_only(self):
        self.assertNotContains(self.teacher.get(f"{self.base}/today"), "김도윤 조퇴")
        self.assertContains(self.teacher.get(f"{self.base}/today?show=school"), "김도윤 조퇴")

    def _monday(self):
        return (self.today - timedelta(days=self.today.weekday())).isoformat()

    def test_week_board_shows_title(self):
        page = self.teacher.get(f"{self.base}/schedule?w={self._monday()}")
        self.assertContains(page, "김도윤 조퇴")
        self.assertContains(page, 'class="ev duty"')

    def test_week_print_uses_symbol(self):
        page = self.teacher.get(f"{self.base}/schedule/print?d={self._monday()}")
        self.assertContains(page, "김도윤 조퇴")
        self.assertTrue(any(x["sym"] == "○" for r in page.context["wrows"] for c in r["cells"] for x in c))

    def test_not_a_event(self):
        from today.models import Event
        self.assertFalse(Event.objects.in_space(self.hub).filter(title__contains="조퇴").exists())


class PurgeTests(Web):
    NOW = datetime(2026, 11, 20, 3, 0, tzinfo=dt_tz.utc)

    def test_older_than_seven_days_deleted(self):
        for days in (8, 7, 0):
            Attendance.objects.create(space=self.hub, date=date(2026, 11, 20) - timedelta(days=days), name=f"{days}일전", kind="연가")
        self.assertEqual(retention.purge(self.NOW, dry_run=True)["attendance"], 1)
        self.assertEqual(Attendance.objects.count(), 3)
        self.assertEqual(retention.purge(self.NOW)["attendance"], 1)
        self.assertEqual(set(Attendance.objects.values_list("name", flat=True)), {"7일전", "0일전"})

    def test_runs_even_if_later_step_fails(self):
        Attendance.objects.create(space=self.hub, date=date(2020, 1, 1), name="옛분", kind="연가")
        with mock.patch("hwpfiles.retention.stuck_sources", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                retention.purge(self.NOW)
        self.assertFalse(Attendance.objects.filter(name="옛분").exists())

    def test_command_output_counts_only(self):
        Attendance.objects.create(space=self.hub, date=date(2020, 1, 1), name="옛분", kind="연가")
        out = io.StringIO()
        call_command("purge", stdout=out)
        self.assertIn("지난 복무 1건", out.getvalue())
        self.assertNotIn("옛분", out.getvalue())


class ScreenTests(Web):
    def test_paste_page_never_sends_raw_on_save(self):
        """바꾸기 폼은 서명 값만 보낸다. 붙여넣는 칸은 그 폼 밖에 있다."""
        page = self.manager.get(self.url).content.decode()
        self.assertIn('id="attPaste"', page)
        out = self.preview().content.decode()
        self.assertIn('name="rows"', out)
        self.assertNotIn('name="paste"', out)
        self.assertIn("사유는 저장하지 않아요", out)
        self.assertLess(page.index("</form>"), page.index('id="attOut"'))

    def test_designate_button_for_manager_only(self):
        page = self.manager.get(f"{self.base}/school").content.decode()
        self.assertIn("복무 입력자로 지정", page)
        self.assertNotIn("복무 입력자로 지정", self.teacher.get(f"{self.base}/school").content.decode())

    def test_toast_counts_only(self):
        self.save()
        page = self.manager.get(f"{self.base}/school")
        self.assertContains(page, "복무 6명을 바꿨어요")
