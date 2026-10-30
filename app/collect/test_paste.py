"""C2 표 붙여넣기: 한글 클립보드 HTML(병합 칸) → TSV → 머리글·받는 사람 칸·입력 칸·형식.

픽스처(fixtures/paste/)는 한글 양식 보관소 hwpx 표를 한글 클립보드 HTML처럼 만든 것(01~12)과
손으로 만든 것(13 디자인 예시, 14 2줄 머리글 + 세로 병합 받는 사람 칸)이다. 실제 한글 2024 표본은 아니다.
"""
import re
from pathlib import Path
from unittest import expectedFailure

from django.test import SimpleTestCase

from .paste import PasteError, html_to_tsv, parse

FIX = Path(__file__).parent / "fixtures" / "paste"
QA = Path(__file__).parent / "fixtures" / "paste_qa"


def load(name):
    return parse(html_to_tsv((FIX / f"{name}.html").read_text(encoding="utf-8")))


def names(fields):
    return [f["name"] for f in fields]


def kinds(p):
    return {f["name"]: f["kind"] for f in p["fields"]}


class HwpFixtureTests(SimpleTestCase):
    """픽스처마다 머리글(병합 머리글은 위아래를 이어 붙임)·받는 사람 칸·입력 칸 수·줄 수."""

    def test_fixture_count(self):
        self.assertGreaterEqual(len(list(FIX.glob("*.html"))), 10)

    def test_01_two_row_header_colspan(self):
        p = load("01_leave_plan")
        months = [f"사용계획 {m}월" for m in range(3, 9)]
        self.assertEqual(p["head"], ["연번", "직급", "성명", "재직기간", "이전 사용실적", *months, "계", "잔여 장기재직휴가 일수"])
        self.assertEqual(p["unit_cols"], ["연번", "직급", "성명"])
        self.assertEqual(len(p["fields"]), 10)
        self.assertEqual(len(p["rows"]), 4)  # 빈 줄·'⦙' 줄 뺌
        self.assertEqual(p["unit_head"], ["연번 직급", "성명"])
        self.assertEqual(p["rows"][0], {"unit": "1 교사", "who": "김○○"})
        k = kinds(p)
        self.assertEqual((k["이전 사용실적"], k["계"], k["잔여 장기재직휴가 일수"]), ("number",) * 3)

    def test_02_header_colspan_and_checkbox_select(self):
        p = load("02_field_trip")
        self.assertEqual(p["head"], ["순", "학년반", "성명", "체험학습 형태", "기간 (부터-까지,일간)",
                                     "결재 담임", "결재 부장", "결재 교감"])
        self.assertEqual(p["unit_cols"], ["순", "학년반", "성명"])
        self.assertEqual(len(p["fields"]), 5)
        self.assertEqual(len(p["rows"]), 8)
        trip = p["fields"][0]
        self.assertEqual(trip["kind"], "select")
        self.assertEqual(trip["options"][0], "현장체험학습")
        self.assertGreaterEqual(len(trip["options"]), 2)

    def test_03_empty_name_columns_become_inputs(self):
        p = load("03_grade_name")
        self.assertEqual(p["head"], ["순", "학년", "성명"])
        self.assertEqual(p["unit_cols"], ["순"])
        self.assertEqual(names(p["fields"]), ["학년", "성명"])
        self.assertEqual(len(p["rows"]), 10)

    def test_04_example_row_and_empty_rows_skipped(self):
        p = load("04_lecture_log")
        self.assertEqual(p["head"], ["날짜", "강의 대상", "시간 (차시)", "강사명", "활동내용", "강사 서명", "확인자 서명"])
        self.assertEqual(p["unit_cols"], [])
        self.assertEqual(len(p["fields"]), 7)
        self.assertEqual(p["rows"], [])
        self.assertEqual(kinds(p)["날짜"], "date")

    def test_05_rowspan_label_column(self):
        p = load("05_bus_check")
        self.assertEqual(p["head"], ["구분", "점검(교육)내용", "점검결과 (실시여부/부적합)", "비고"])
        self.assertEqual(p["unit_cols"], ["구분", "점검(교육)내용"])
        self.assertEqual(len(p["rows"]), 13)
        self.assertTrue(p["rows"][1]["unit"].startswith("운전자 - 운전자 음주여부"))  # 세로 병합 '운전자'가 둘째 줄에도
        f = p["fields"][0]
        self.assertEqual((f["kind"], f["options"]), ("select", ["예", "아니오"]))

    def test_06_filled_left_columns_are_prefilled(self):
        p = load("06_tutoring")
        self.assertEqual(p["unit_cols"], ["순", "날짜", "지도시간", "지도내용"])
        self.assertEqual(names(p["fields"]), ["비고"])
        self.assertEqual(len(p["rows"]), 12)

    def test_07_three_header_rows_and_merged_data_columns(self):
        p = load("07_period_grid")
        self.assertEqual(len(p["head"]), 11)  # 자료 줄에서 늘 붙어 있는 열 하나를 합침
        self.assertEqual(p["head"][0], "구분 순")
        self.assertEqual(p["head"][2], "1 9:30 ~ 10:10")
        self.assertEqual(p["unit_cols"], ["구분 순"])
        self.assertEqual(len(p["fields"]), 10)
        self.assertEqual(len(p["rows"]), 5)

    def test_08_total_rows_dropped(self):
        p = load("08_budget")
        self.assertEqual(p["head"], ["구분", "예산 구분", "2023년", "2024년", "2025년", "2026년", "2027년", "합계"])
        self.assertEqual(p["unit_cols"], ["구분", "예산 구분"])
        self.assertEqual(len(p["rows"]), 8)
        self.assertNotIn("합계", " ".join(r["unit"] for r in p["rows"]))
        self.assertEqual(set(kinds(p).values()), {"number"})

    def test_09_title_row_dropped(self):
        p = load("09_timetable")
        self.assertEqual(p["head"], ["요일 시간", "월", "화", "수", "목", "금"])
        self.assertEqual(p["unit_cols"], ["요일 시간"])
        self.assertEqual(len(p["rows"]), 5)

    def test_10_rowspan_in_data(self):
        p = load("10_projects")
        self.assertEqual(p["unit_cols"], ["학년", "프로젝트명(활동명)", "관련교과"])
        self.assertEqual(names(p["fields"]), ["단원", "시기(월)"])
        self.assertEqual(len(p["rows"]), 9)
        self.assertTrue(p["rows"][3]["unit"].startswith("3학년 마을 친구"))

    def test_11_filled_list(self):
        p = load("11_events")
        self.assertEqual(p["unit_cols"], ["행사명", "일자 및 기간"])
        self.assertEqual(names(p["fields"]), ["비고"])
        self.assertEqual(len(p["rows"]), 8)

    def test_12_grade_colspan_subheader(self):
        p = load("12_teacher_hours")
        self.assertEqual(len(p["head"]), 14)
        self.assertEqual(p["head"][1:3], ["1학년 (2학급) 교과", "1학년 (2학급) 주당 시수"])
        self.assertEqual(p["unit_cols"], ["교과전담"])
        self.assertEqual(len(p["fields"]), 13)
        self.assertEqual(len(p["rows"]), 11)  # '계' 두 줄 뺌
        self.assertEqual(kinds(p)["1학년 (2학급) 주당 시수"], "number")

    def test_13_design_sample_picks_biggest_table(self):
        p = load("13_design_sample")  # 결재란 표가 앞에 있어도 큰 표를 읽는다
        self.assertEqual(p["head"], ["반", "담임", "참가(명)", "불참 (명)", "비고"])
        self.assertEqual(p["unit_cols"], ["반", "담임"])
        self.assertEqual(p["unit_head"], ["반", "담임"])
        self.assertEqual([(f["name"], f["kind"]) for f in p["fields"]],
                         [("참가(명)", "number"), ("불참 (명)", "number"), ("비고", "text")])
        self.assertEqual([r["unit"] for r in p["rows"]], ["4-1", "4-2", "4-3", "4-4", "4-5"])

    def test_14_two_row_header_rowspan_unit(self):
        p = load("14_two_row_header")
        self.assertEqual(p["head"], ["학년", "반", "담임", "희망 인원 남", "희망 인원 여", "희망 프로그램", "제출일"])
        self.assertEqual(p["unit_cols"], ["학년", "반", "담임"])
        self.assertEqual(p["unit_head"], ["학년 반", "담임"])
        self.assertEqual([r["unit"] for r in p["rows"]], ["4학년 1", "4학년 2", "4학년 3"])  # 빈 줄·소계 뺌
        self.assertEqual([f["kind"] for f in p["fields"]], ["number", "number", "select", "date"])


class HtmlToTsvTests(SimpleTestCase):
    def test_no_table_gives_empty(self):
        self.assertEqual(html_to_tsv("<p>표 없음</p>"), "")

    def test_nbsp_br_paragraphs_become_single_spaces(self):
        tsv = html_to_tsv("<table><tr><td><p>희망</p><p>프로그램</p></td><td>참가<br>인원</td><td>&nbsp;</td></tr>"
                          "<tr><td>4-1</td><td></td><td></td></tr></table>")
        self.assertEqual(tsv.split("\n")[0], "희망 프로그램\t참가 인원\t")

    def test_rowspan_copy_blowup_raises(self):
        cells = "".join(f'<td rowspan="1000">{"가" * 900}</td>' for _ in range(60))
        html = f"<table><tr>{cells}</tr>" + "<tr><td>x</td></tr>" * 999 + "</table>"
        self.assertLess(len(html), 100_000)
        with self.assertRaisesMessage(PasteError, "너무 커요"):
            html_to_tsv(html)

    def test_deep_nesting_and_long_cell_capped(self):
        html = "<table><tr><td>반</td><td>" + "<table><tr><td>" * 200 + "깊" + "</td></tr></table>" * 200 + "</td></tr>"
        html += "<tr><td>" + "가" * 50_000 + "</td><td></td></tr></table>"
        lines = html_to_tsv(html).split("\n")
        self.assertEqual(lines[0], "반\t깊")
        self.assertLessEqual(len(lines[1]), 201)

    def test_huge_spans_are_capped(self):
        tsv = html_to_tsv('<table><tr><td rowspan="999999" colspan="999999">반</td><td>값</td></tr></table>')
        self.assertLess(len(tsv), 100_000)

    def test_script_and_style_text_ignored(self):
        tsv = html_to_tsv("<style>td{}</style><table><tr><td>반<script>x()</script></td><td>값</td></tr></table>")
        self.assertEqual(tsv, "반\t값")


class FixturePrivacyTests(SimpleTestCase):
    def test_name_columns_masked(self):
        # 성명·이름·강사명 열 값은 ○로 가렸거나 비어 있어야 한다(표본 hwpx의 실명이 새지 않게)
        files = [f for d in ("paste", "paste_qa", "paste_qa2") for f in (FIX.parent / d).glob("*.html")]
        checked = 0
        for f in files:
            lines = [r.split("\t") for r in html_to_tsv(f.read_text(encoding="utf-8")).split("\n")]
            for c, h in enumerate(lines[0]):
                if re.fullmatch(r"(성명|이름|강사명|교사명)", h.replace(" ", "")):
                    for r in lines[1:]:
                        v = r[c] if c < len(r) else ""
                        checked += 1
                        self.assertTrue(not v or "○" in v, f"{f.name}: {h}")
        self.assertGreater(checked, 0)

    def test_row_cell_count_capped(self):
        tsv = html_to_tsv("<table><tr>" + "<td>a</td>" * 500 + "</tr></table>")
        self.assertEqual(len(tsv.split("\t")), 60)

    def test_no_phone_rrn_email_in_fixtures(self):
        pii = re.compile(r"\d{2,3}-\d{3,4}-\d{4}|\d{6}-[1-4]\d{6}|[\w.+-]+@[\w-]+\.[\w.]+")
        files = list(FIX.glob("*.html")) + list((FIX.parent / "paste_qa").glob("*.html"))
        self.assertGreaterEqual(len(files), 20)
        for f in files:
            self.assertIsNone(pii.search(f.read_text(encoding="utf-8")), f.name)


class TsvTests(SimpleTestCase):
    """엑셀·구글 시트·고친 TSV(text/plain 탭)도 같은 규칙."""

    def test_tsv_with_blank_and_total_rows(self):
        p = parse("반\t담임\t참가(명)\t참가 여부\r\n4-1\t\t\t\r\n\r\n4-2\t\t\t\r\n합 계\t\t\t\r\n…\r\n")
        self.assertEqual(p["unit_cols"], ["반", "담임"])
        self.assertEqual([(f["name"], f["kind"]) for f in p["fields"]], [("참가(명)", "number"), ("참가 여부", "select")])
        self.assertEqual(p["fields"][1]["options"], ["예", "아니오"])
        self.assertEqual(len(p["rows"]), 2)

    def test_prototype_sample(self):
        p = parse("학년반\t담임\t희망 인원\t희망 프로그램\t비고\n1-1\t강지은\t\t\t\n1-2\t윤서준\t\t\t\n2-1\t최수아\t\t\t")
        self.assertEqual(p["unit_cols"], ["학년반", "담임"])
        self.assertEqual([f["kind"] for f in p["fields"]], ["number", "select", "text"])
        self.assertEqual(p["rows"][0], {"unit": "1-1", "who": "강지은"})

    def test_class_values_and_weekday_su(self):
        p = parse("교시\t수\t수업시수\t학생수\n1\t6-1\t창체(1)\t\n2\t4-2\t교과(2)\t\n3\t\t\t")
        self.assertEqual([f["kind"] for f in p["fields"]], ["text", "text", "number"])

    def test_kind_from_values(self):
        p = parse("반\t예산\t낸 때\t메모\n4-1\t12,000\t2026-11-02\t\n4-2\t3000\t2026. 11. 3.\t\n4-3\t\t\t")
        self.assertEqual([f["kind"] for f in p["fields"]], ["number", "date", "text"])

    def test_select_options_from_values(self):
        p = parse("반\t희망\n4-1\t예\n4-2\t아니오\n4-3\t예")
        self.assertEqual((p["fields"][0]["kind"], p["fields"][0]["options"]), ("select", ["예", "아니오"]))

    def test_duplicate_and_blank_headers(self):
        p = parse("반\t비고\t비고\t\n4-1\t\t\tx")
        self.assertEqual(p["head"], ["반", "비고", "비고 (2)", "4번째 칸"])

    def test_text_without_tabs_is_error(self):
        # 한글 표를 글자로만 받으면 칸마다 한 줄(실측 649줄×1칸)
        with self.assertRaisesMessage(PasteError, "칸 구분"):
            parse("반\n담임\n참가\n4-1\n\n")

    def test_no_input_columns_is_error(self):
        with self.assertRaisesMessage(PasteError, "받을 칸"):
            parse("반\t담임\n4-1\t김가람")

    def test_empty_is_error(self):
        with self.assertRaises(PasteError):
            parse(" \n\t\n")

    def test_long_cells_trimmed(self):
        p = parse("반\t메모\n" + "가" * 500 + "\t")
        self.assertLessEqual(len(p["rows"][0]["unit"]), 100)
        self.assertLessEqual(len(p["head"][0]), 100)


def load_qa(name):
    return parse(html_to_tsv((QA / f"{name}.html").read_text(encoding="utf-8")))


class QaHoldoutTests(SimpleTestCase):
    """QA 판정용 표 10개(fixtures/paste_qa/): 기존 픽스처에 쓰지 않은 보관소 hwpx의 모으기 양식 표를
    make_paste_fixtures.py와 같은 방식(한글 클립보드 HTML 흉내, 이름은 ○○○, 16줄까지)으로 만든 것.
    기준(사람이 보기에 맞음): 머리글이 사람이 읽는 열 이름과 같고(빠짐·중복·자료가 섞인 열 없음),
    내용이 이미 적힌 열은 미리 채움·빈 열은 입력 칸, 줄 수는 빈 줄·합계 줄을 뺀 자료 줄 수.
    처음 판정에서 틀린 q03·q07·q10은 고친 뒤 회귀 테스트로 둔다. 실제 한글 2024 표본은 아니다.
    """

    def test_q01_checklist(self):
        p = load_qa("q01_vacation_check")
        self.assertEqual(p["head"], ["번호", "점검사항", "O X", "비고"])
        self.assertEqual(p["unit_cols"], ["번호", "점검사항"])
        self.assertEqual(names(p["fields"]), ["O X", "비고"])
        self.assertEqual(len(p["rows"]), 15)

    def test_q02_rowspan_category(self):
        p = load_qa("q02_safety_check")
        self.assertEqual(p["head"], ["점검분야", "점검사항", "이상여부", "비고"])
        self.assertEqual(p["unit_cols"], ["점검분야", "점검사항"])
        self.assertEqual([(f["name"], f["kind"]) for f in p["fields"]], [("이상여부", "select"), ("비고", "text")])
        self.assertEqual(len(p["rows"]), 15)
        self.assertTrue(p["rows"][1]["unit"].startswith("건축물 옥상"))  # 세로 병합 '건축물'이 둘째 줄에도

    def test_q03_colspan_header_over_rowspan_data(self):
        # 머리글 '영역'(가로 2칸)·'검토항목'(가로 2칸) 밑이 자료 줄인데, 밑이 갈라져 있다고 첫 자료 줄을 머리글로 먹는다.
        # 고치기 전: 머리글에 질문 문장이 붙고 받는 사람 줄 0개.
        p = load_qa("q03_digital_review")
        self.assertFalse(any("?" in h for h in p["head"]), p["head"])
        self.assertIn("검토완료", names(p["fields"]))
        self.assertIn("해당 없음", names(p["fields"]))
        self.assertGreaterEqual(len(p["rows"]), 11)

    def test_q04_budget_total_row(self):
        p = load_qa("q04_budget_detail")
        self.assertEqual(p["head"], ["연번", "내용", "세부 산출 내역", "금액(원)", "비고"])
        self.assertEqual(p["unit_cols"], ["연번"])
        self.assertEqual(kinds(p)["금액(원)"], "number")
        self.assertEqual(len(p["rows"]), 6)  # '계' 줄 뺌

    def test_q05_empty_signup_form(self):
        p = load_qa("q05_instructor_form")
        self.assertEqual(p["head"], ["소속", "성명", "아이톡톡계정", "강좌명", "운영차시", "휴대폰번호"])
        self.assertEqual(p["unit_cols"], ["성명"])  # design PRE 정확 일치
        self.assertEqual(names(p["fields"]), ["소속", "아이톡톡계정", "강좌명", "운영차시", "휴대폰번호"])
        self.assertEqual(p["rows"], [])

    def test_q06_empty_form_wrapped_header(self):
        p = load_qa("q06_program_review")
        self.assertEqual([h.replace(" ", "") for h in p["head"]], ["프로그램명", "활동내용", "만족도", "비고"])
        self.assertEqual(p["unit_cols"], [])
        self.assertEqual(len(p["fields"]), 4)
        self.assertEqual(p["rows"], [])

    def test_q07_diagonal_header_name_by_date(self):
        # 대각선 머리글 '일 시 / 성 명' + 첫 날짜 열만 적힌 표. 이름 열만 미리 채움이어야 하는데
        # '왼쪽부터 다 채워진 열' 규칙이 첫 날짜 열(△○◎)까지 받는 사람 칸으로 가져간다.
        p = load_qa("q07_date_grid")
        self.assertEqual(len(p["unit_cols"]), 1)
        self.assertIn("9.18.(월)", names(p["fields"]))
        self.assertEqual(len(p["rows"]), 9)

    def test_q08_score_sheet(self):
        p = load_qa("q08_score_sheet")
        self.assertEqual(p["head"], ["지표", "심사관점", "심사기준", "배점", "점수", "증빙자료"])
        self.assertEqual(p["unit_cols"], ["지표", "심사관점", "심사기준", "배점"])
        self.assertEqual([(f["name"], f["kind"]) for f in p["fields"]], [("점수", "number"), ("증빙자료", "text")])
        self.assertEqual(len(p["rows"]), 15)
        self.assertTrue(p["rows"][1]["unit"].startswith("학교폭력 예방활동 (40점)"))

    def test_q09_pledge(self):
        p = load_qa("q09_driver_pledge")
        self.assertEqual(p["head"], ["연번", "확약사항", "확인"])
        self.assertEqual(p["unit_cols"], ["연번", "확약사항"])
        self.assertEqual(names(p["fields"]), ["확인"])
        self.assertEqual(len(p["rows"]), 9)

    def test_q10_colspan_columns_with_summary_rows(self):
        # 모든 칸이 가로 3·3·2칸 병합인 표에 아래 '구분/인원' 요약 두 줄이 붙어 있다.
        # 요약 줄 때문에 '늘 붙은 열 합치기'가 안 되어 머리글이 '교시 (2)'·'수업 교사 (3)'처럼 중복된다.
        p = load_qa("q10_period_observe")
        self.assertEqual(p["head"][:3], ["교시", "수업 교사", "비고"])
        self.assertEqual(p["unit_cols"], ["교시", "수업 교사"])
        self.assertEqual(names(p["fields"])[0], "비고")


QA2 = Path(__file__).parent / "fixtures" / "paste_qa2"


def load_qa2(name):
    return parse(html_to_tsv((QA2 / f"{name}.html").read_text(encoding="utf-8")))


class QaHoldout2Tests(SimpleTestCase):
    """과적합 확인용 표 10개(fixtures/paste_qa2/): fixtures/paste·paste_qa와 다른 보관소 hwpx의 모으기형 표.
    만드는 방식·판정 기준은 QaHoldoutTests와 같다(머리글·받는 사람 칸·입력 칸·줄 수). 형식 추정 오류는 따로 적는다.
    머리글이 빈 왼쪽 위 칸은 '1번째 칸'이 맞다(원본도 비어 있음).
    r05·r10 구조 expectedFailure는 리드 결정으로 둔다: 구조 규칙을 더 덧대면 다른 표가 깨질 위험이 커서(빈 열 오른쪽 채워진 열, 반복 머리글 줄).
    """

    def test_r01_room_booking(self):
        p = load_qa2("r01_room_booking")
        self.assertEqual(p["head"], ["1번째 칸", "월", "화", "수", "목", "금", "비고"])
        self.assertEqual(p["unit_cols"], ["1번째 칸"])
        self.assertEqual(len(p["fields"]), 6)
        self.assertEqual(len(p["rows"]), 9)

    def test_r01_weekday_su_is_not_number(self):
        # 고치기 전: 요일 '수'가 NUM_NAME의 '수$'(인원수·학생수)에 걸려 숫자가 됐다. 값은 '영어(4) 골프(6)…'.
        self.assertEqual(kinds(load_qa2("r01_room_booking"))["수"], "text")

    def test_r02_two_row_header_checkmarks(self):
        p = load_qa2("r02_material_check")
        self.assertEqual(p["head"], ["순", "학습 주제 대주제", "학습 주제 세부 내용", "자료 유형* 수업 지도안",
                                     "자료 유형* 수업 꾸러미", "자료 유형* 수업 요약 영상"])
        self.assertEqual(p["unit_cols"], ["순"])
        self.assertEqual(len(p["fields"]), 5)
        self.assertEqual(len(p["rows"]), 5)

    def test_r03_checker_sign(self):
        p = load_qa2("r03_checker_sign")
        self.assertEqual(p["head"], ["1번째 칸", "성명", "확인"])
        self.assertEqual(p["unit_cols"], ["1번째 칸", "성명"])  # design PRE: 성명은 미리 채움
        self.assertEqual(names(p["fields"]), ["확인"])
        self.assertEqual([r["unit"] for r in p["rows"]], ["1차 점검자", "2차 점검자", "3차 점검자"])

    def test_r04_empty_travel_form(self):
        p = load_qa2("r04_travel_form")
        self.assertEqual(names(p["fields"]), ["여행기간", "여행국명", "목적", "비고"])
        self.assertEqual((p["unit_cols"], p["rows"]), ([], []))

    @expectedFailure
    def test_r05_filled_column_right_of_empty_column(self):
        # '관련 교과'(빈 열) 오른쪽의 '활용 에듀테크'는 다 채워져 있는데, '왼쪽부터 채워진 열'만 미리 채움이라
        # 입력 칸이 되고 적혀 있던 값(PPT·구글어스…)이 버려진다.
        p = load_qa2("r05_project_plan")
        self.assertIn("활용 에듀테크", p["unit_cols"])
        self.assertEqual(names(p["fields"]), ["관련 교과"])

    def test_r06_lesson_list(self):
        p = load_qa2("r06_lesson_list")
        self.assertEqual((p["head"], p["unit_cols"], names(p["fields"])), (["순", "학습내용", "비고"], ["순", "학습내용"], ["비고"]))
        self.assertEqual(len(p["rows"]), 15)

    def test_r07_activity_log(self):
        p = load_qa2("r07_activity_log")
        self.assertEqual(p["unit_cols"], ["순", "일시", "활동 내용"])
        self.assertEqual(names(p["fields"]), ["비고"])
        self.assertEqual(len(p["rows"]), 15)

    def test_r08_two_row_header_grades(self):
        p = load_qa2("r08_event_grades")
        self.assertEqual(p["head"][3:9], [f"학년 {g}" for g in range(1, 7)])
        self.assertEqual(p["unit_cols"], ["학교교육활동", "일시", "활동내용"])
        self.assertEqual(len(p["rows"]), 14)

    def test_r09_afterschool_rowspan_period(self):
        p = load_qa2("r09_afterschool")
        self.assertEqual(p["head"], ["1번째 칸", "월", "화", "수", "목", "금"])
        self.assertEqual([r["unit"] for r in p["rows"]], ["1", "2", "2", "3", "3", "4", "4", "5", "6"])

    @expectedFailure
    def test_r10_repeated_header_rows_are_not_recipients(self):
        # 주마다 '교시 | 10/7(월) | …' 머리글 줄이 다시 나오는 표. 반복 머리글 줄이 받는 사람 줄('교시')이 된다.
        p = load_qa2("r10_period_assign")
        self.assertNotIn("교시", [r["unit"] for r in p["rows"]])

    def test_r10_class_values_are_not_dates(self):
        # 고치기 전: 값 '6-1'·'4-1'(학년-반)이 DATE_VAL의 '월-일'에 걸려 날짜가 됐다.
        self.assertEqual(kinds(load_qa2("r10_period_assign"))["9/3(화)"], "text")
