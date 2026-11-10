"""P3 이관 도구. 픽스처는 모두 직접 만든 가상 데이터(실제 이름·아이디·해시 없음).

불변식
- 학교마다 공간 하나, 데이터는 구 학교 코드(wr_3·mb_1)의 공간에만 들어간다(다른 학교로 새지 않음).
- '나' 일정은 작성자 말고는(관리자 포함) 못 본다. 작성자 자리가 없으면 버린다.
- 이미 있는 아이디·이미 공간이 있는 학교는 덮어쓰지 않고 멈춘다.
- 다시 돌려도 늘지 않고, 새 행만 더한다. 마른 실행은 아무것도 남기지 않는다.
- 보고서에는 이름·아이디가 없다.
"""
import io
import json
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.hashers import G5PBKDF2PasswordHasher
from hwpfiles.tests import sample_hwpx
from notices.models import Notice
from spaces.models import Attendance, Member, School, Space
from today.models import Event, reachable_q

from .models import LegacyMap
from .importer import Importer
from .rules import Rejected, duty_name, duty_of, html_to_text, judge_file, seat_fields, unique_name

KST = ZoneInfo("Asia/Seoul")
TESTDATA = Path(__file__).resolve().parent.parent / "notices" / "testdata"
PASSWORD = "가상비밀번호123"
PDF = b"%PDF-1.7\n%%EOF\n"
A, B, C = "7000001", "7000002", "7000003"


def g5(password, salt="가짜솔트abc"):
    return G5PBKDF2PasswordHasher().encode(password, salt).split("$", 1)[1]


def member(no, mb_id, name, school, group, mb_4="", mb_5="1", login="2026-05-01 08:00:00", joined=None, **kw):
    return {"mb_no": no, "mb_id": mb_id, "mb_name": name, "mb_password": kw.get("pw", g5(PASSWORD)), "mb_1": school,
            "mb_2": "", "mb_3": str(group), "mb_4": mb_4, "mb_5": mb_5, "mb_addr2": "", "mb_level": kw.get("level", 2),
            "mb_datetime": joined or f"2023-03-{no:02d} 10:00:00", "mb_today_login": login,
            "mb_leave_date": kw.get("leave", ""), "mb_intercept_date": ""}


def write(wr_id, school, ch, subject, by, start="2026-05-01", end="", content="", html=False, parent=None, **kw):
    return {"wr_id": wr_id, "wr_parent": parent or wr_id, "wr_is_comment": 1 if parent else 0, "mb_id": by,
            "wr_name": kw.get("wr_name", "가상작성자"), "wr_1": start, "wr_2": end, "wr_3": school, "wr_4": str(ch),
            "wr_5": "", "wr_6": "", "wr_7": str(kw.get("wr_7", "")), "wr_8": kw.get("wr_8", ""), "wr_9": "",
            "wr_10": kw.get("wr_10", ""), "wr_subject": subject, "wr_content": content, "wr_option": "html1" if html else "",
            "wr_datetime": kw.get("at", "2026-04-01 09:00:00")}


def bf(table, wr_id, no, source, stored, data=None):
    return {"bo_table": table, "wr_id": wr_id, "bf_no": no, "bf_source": source, "bf_file": stored,
            "bf_filesize": len(data or b""), "bf_datetime": "2026-04-01 09:30:00", "_data": data}


def zip_bytes():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("a.txt", "가상")
    return out.getvalue()


def fixture():
    members = [
        member(1, "teach_one", "가상하나", A, 10, "1반"),
        member(2, "teach_two", "가상하나", A, 10, "1반"),          # 같은 반·같은 이름 → (2)
        member(3, "office_3", "가상셋", A, 11, "교직원"),
        member(4, "moved_4", "가상넷", A, 12, "교사"),            # 전출 그룹: 계정만
        member(5, "nurse_5", "가상다섯", A, 13, "보건교사"),
        member(6, "left_6", "가상여섯", A, 10, "2반", leave="20260101"),  # 탈퇴: 계정도 없음
        member(7, "notjoin_7", "가상일곱", A, 10, "", mb_5="0"),     # 학교 가입 안 함: 계정만
        member(8, "bteach_8", "가상여덟", B, 20, "2반", login="2026-04-01 08:00:00"),
        member(9, "cteach_9", "가상아홉", C, 30, "1반", login="2025-05-01 08:00:00"),  # C는 대상 아님
        member(10, "nohash_10", "가상열", C, 30, "", mb_5="0", pw=""),
        member(11, "nogroup_11", "가상열하나", A, "", "교사"),
        member(13, "root_13", "가상운영", A, 11, "교감", level=10),          # 운영 계정: 계정·자리 없음
        member(14, "neis_sync_bot", "가상봇", A, 11, ""),
        member(15, "google:x1", "가상사칭", A, 10, "3반"),                 # 아이디 규칙 밖: 멈춤
    ]
    groups = [{"group_id": i, "group_name": n, "school_id": s, "sort_order": 0, "is_active": 1, "created_at": "", "updated_at": ""}
              for i, n, s in [(10, "4학년", A), (11, "교무실", A), (12, "전출", A), (13, "보건실", A), (20, "1학년", B), (30, "2학년", C)]]
    schools = [{"id": s, "code_sido": "E10", "name": n, "grade": "초"} for s, n in [(A, "가상초"), (B, "나상초"), (C, "다상초")]]
    body = "<p>첫&nbsp;줄</p>\n<p>둘<br>셋</p><img src=x>"
    goteacher = [
        write(100, A, 0, "운동회", "teach_one", "2026-05-01", "2026-05-02", body, html=True),
        write(101, A, 1, "운동회", "teach_one", "2026-05-01", "2026-05-02", body, html=True, at="2026-04-02 09:00:00"),
        write(102, A, 3, "내 일정", "teach_one"),
        write(103, A, 3, "자리 없는 사람 일정", "moved_4"),
        write(104, A, 4, "그룹 일정", "teach_one", wr_7=10),
        write(105, A, 4, "옛 코드 일정", "teach_one", wr_7=3),
        write(106, A, 4, "모르는 그룹", "office_3", wr_7=999),
        write(107, A, 7, "과학실 예약", "nurse_5"),
        write(108, A, 2, "가상하나 | 출장(08:30~16:30,가상학교,가상사유)", "office_3", "2026-05-04", "2026-05-06"),
        write(109, A, 2, "가상셋 | 병가", "office_3"),
        write(110, A, 2, "가상셋 | 시간 외 근무", "office_3"),
        write(116, A, 2, "가상셋 | 전문적학습공동체 14:00~16:30", "office_3", "2026-05-07"),
        write(141, A, 2, "가상하나 선생님(가상장소) | 출장", "office_3", "2026-05-08"),   # 명부 이름만
        write(142, A, 2, "(가상사유)가상열둘 교사 | 연가", "office_3", "2026-05-09"),     # 괄호·직함 떼고 이름
        write(143, A, 2, "3학년 협의 | 출장", "office_3", "2026-05-10"),                  # 이름 판별 못 함
        write(144, "", 0, "운영자 일정", "teach_one"),
        write(111, A, 0, "학사일정", "neis_sync_bot"),
        write(112, A, 0, "읽기 제한", "teach_one", wr_8="office_3"),
        write(113, A, 0, "날짜 깨짐", "teach_one", "병원 및"),
        write(200, B, 0, "나상초 행사", "bteach_8"),
        write(300, A, 0, "", "office_3", content="댓글 <- 화살표", parent=101, at="2026-04-03 10:00:00"),
        write(301, A, 0, "", "office_3", content="복무 댓글", parent=108),
    ]
    board = [
        write(400, A, "", "가상 안내", "teach_one", "", "", "<p>안내 본문</p>", html=True, at="2025-03-02 08:00:00"),
        write(401, A, "", "", "office_3", "", "", "게시판 댓글", parent=400),
    ]
    files = [
        bf("goteacher", 101, 0, "사진.jfif", "f1", (TESTDATA / "real.jpg").read_bytes()),
        bf("goteacher", 100, 0, "문서.hwp", "f2", sample_hwpx()),
        bf("goteacher", 100, 1, "묶음.zip", "f3", zip_bytes()),
        bf("goteacher", 102, 0, "없음.pdf", "missing", None),
        bf("goteacher", 108, 0, "복무.pdf", "f5", b"%PDF-1.7\n%%EOF\n"),
        bf("goteacher", 300, 0, "회의록.hwp", "c0", sample_hwpx()),
        bf("goteacher", 300, 1, "가.pdf", "c1", PDF),
        bf("goteacher", 300, 2, "나.zip", "c2", zip_bytes()),
        bf("goteacher", 300, 3, "다.pdf", "c3", PDF),
        bf("goteacher", 300, 4, "라.pdf", "c4", PDF),
        bf("goteacher", 301, 0, "복무댓글.pdf", "c5", PDF),
        bf("goteacher", 104, 0, "탈출.pdf", "../../g5_member.jsonl", None),
        *[bf("goteacher", 105, i, f"자료{i}.pdf", f"e{i}", PDF) for i in range(6)],
        bf("free_school", 400, 0, "안내.docx", "f6", (TESTDATA / "real.docx").read_bytes()),
    ]
    return {"g5_member": members, "contact_group": groups, "school": schools, "g5_write_goteacher": goteacher,
            "g5_write_free_school": board, "g5_board_file": files,
            "g5_write_channel": [{"wr_id": 7, "wr_3": A, "wr_subject": "특별실"}],
            "g5_write_go_admin": [{"wr_id": 1, "wr_3": A, "mb_id": "teach_one", "wr_is_comment": 0}]}


def dump_to(folder, data):
    folder = Path(folder)
    for table, rows in data.items():
        with open(folder / f"{table}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                body = {k: v for k, v in r.items() if k != "_data"}
                f.write(json.dumps(body, ensure_ascii=False) + "\n")
                if r.get("_data") is not None:
                    path = folder / "files" / r["bo_table"] / r["bf_file"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(r["_data"])


class RuleTests(SimpleTestCase):
    def test_seat_fields(self):
        self.assertEqual(seat_fields("4학년", "3반"), {"group": "g4", "cls": "4-3", "position": ""})
        self.assertEqual(seat_fields("4학년", "전담"), {"group": "g4", "cls": "4-전담", "position": ""})
        self.assertEqual(seat_fields("4학년", "교과"), {"group": "g4", "cls": "4-전담", "position": "교과"})
        self.assertEqual(seat_fields("관리자", "교감"), {"group": "office", "cls": "교무실", "position": "교감"})
        self.assertEqual(seat_fields("교무실", "교직원")["position"], "")
        self.assertEqual(seat_fields("행정실", "주무관"), {"group": "admin", "cls": "행정실", "position": "주무관"})
        self.assertEqual(seat_fields("영양사실", "영양사")["group"], "meal")
        self.assertEqual(seat_fields("병설유치원", "교사"), {"group": "support", "cls": "기타/지원", "position": "병설유치원 · 교사"})
        self.assertEqual(seat_fields(None, "교사"), {"group": "support", "cls": "기타/지원", "position": ""})
        self.assertIsNone(seat_fields("전출", "교사"))
        self.assertIsNone(seat_fields("휴가&휴직", ""))

    def test_unique_name(self):
        taken = set()
        self.assertEqual([unique_name("가상", "4-1", taken) for _ in range(3)], ["가상", "가상(2)", "가상(3)"])
        self.assertEqual(unique_name("가상", "4-2", taken), "가상")

    def test_html_to_text(self):
        self.assertEqual(html_to_text("<p>첫&nbsp;줄</p>\n<p>둘<br>셋</p><img src=x>"), "첫 줄\n둘\n셋\n[그림]")
        self.assertEqual(html_to_text("<table><tr><td>가</td><td>나</td></tr><tr><td>다</td></tr></table>"), "가\t나\n다")
        self.assertEqual(html_to_text("&lt;안내&gt; &amp; <script>x()</script>끝"), "<안내> & 끝")

    def test_duty(self):
        self.assertEqual(duty_of("가상 | 출장(08:30~16:30,가상학교,사유)"), (("가상", "출장", "08:30~16:30"), ""))
        self.assertEqual(duty_of("가상 | 병가")[0], ("가상", "휴가", "종일"))
        self.assertEqual(duty_of("가상 | 육아시간 9:00-11:00")[0], ("가상", "부재", "09:00~11:00"))
        self.assertEqual(duty_of("가상 | 조퇴")[0], ("가상", "조퇴", ""))
        self.assertEqual(duty_of("가상 | 연가 후 출장")[0][1], "연가")
        self.assertEqual(duty_of("가상 | 협의회")[0], ("가상", "출장", "종일"))  # D12 활동은 출장
        self.assertEqual(duty_of("가상 | 사전답사 9:30~12:00")[0], ("가상", "출장", "09:30~12:00"))
        self.assertEqual(duty_of("가상 | 전일 부재")[0], ("가상", "부재", ""))
        self.assertEqual(duty_of("가상 | 부재")[0][1], "부재")
        self.assertEqual(duty_of("가상 | 연차")[0], ("가상", "연가", "종일"))
        self.assertEqual(duty_of("가상 | 출장 후 초과근무")[0][1], "출장")
        for text in ("시간외근무", "시간 외 근무 18:00~20:00", "초과 근무"):
            self.assertEqual(duty_of(f"가상 | {text}"), (None, "시간외·초과근무"))
        self.assertEqual(duty_of("가상 | "), (None, "복무 내용 없음"))
        self.assertEqual(duty_of(" | 출장"), (None, "복무 이름 없음"))
        self.assertEqual(duty_of("출장"), (None, "복무 이름 없음"))

    def test_duty_name(self):
        roster = ["가상하나", "가상둘"]
        self.assertEqual(duty_name("가상하나 선생님(가상장소)", roster), "가상하나")
        self.assertEqual(duty_name("가상둘·가상하나", roster), "가상둘")  # 여럿이면 가장 앞
        self.assertEqual(duty_name("[가상사유] 가상셋 주무관", roster), "가상셋")
        self.assertEqual(duty_name("가상셋교감님", roster), "가상셋")
        self.assertIsNone(duty_name("3학년 협의", roster))
        self.assertIsNone(duty_name("가상사유가길게붙은이름", roster))

    def test_judge_file(self):
        with tempfile.TemporaryDirectory() as d:
            def put(data):
                p = Path(d) / "x"
                p.write_bytes(data)
                return p
            self.assertEqual(judge_file(put(sample_hwpx()), "문서.hwp"), ("hwpx", "문서.hwpx"))
            self.assertEqual(judge_file(put((TESTDATA / "real.jpg").read_bytes()), "사진.jfif"), ("jpg", "사진.jpg"))
            self.assertEqual(judge_file(put((TESTDATA / "real.jpg").read_bytes()), "사진.jpeg"), ("jpg", "사진.jpeg"))
            with self.assertRaises(Rejected):
                judge_file(put(zip_bytes()), "묶음.zip")
            with self.assertRaises(Rejected):
                judge_file(put((TESTDATA / "real.jpg").read_bytes()), "가짜.pdf")


class ImportBase(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        self.media = Path(media)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)
        self.data = fixture()
        dump_to(self.dir, self.data)

    def run_import(self, *args):
        out = io.StringIO()
        call_command("import_legacy", str(self.dir), *args, stdout=out)
        return out.getvalue()

    def space(self, code):
        return Space.objects.get(school__neis_code=code, grade=0)

    def seat(self, mb_id):
        return Member.objects.get(user__username=mb_id)

    def event(self, wr_id):
        return Event.objects.get(pk=LegacyMap.objects.get(kind="event", legacy_id=str(wr_id)).obj_id)


class ImportTests(ImportBase):
    def setUp(self):
        super().setUp()
        self.report = self.dir / "report.csv"
        self.out = self.run_import("--report", str(self.report))

    def test_schools_are_paid_school_spaces_for_d1_targets_only(self):
        self.assertEqual(sorted(School.objects.values_list("neis_code", "plan", "paid_until")),
                         [(A, "school", date(2027, 2, 28)), (B, "school", date(2027, 2, 28))])
        self.assertEqual(Space.objects.filter(kind="school", grade=0).count(), 2)

    def test_accounts_for_everyone_but_left_with_old_hash_login(self):
        users = get_user_model().objects
        self.assertEqual(users.count(), 10)  # 탈퇴 1명 빼고 전원(이관 안 하는 C 학교 포함, D10)
        self.assertFalse(users.filter(username="left_6").exists())
        u = users.get(username="teach_one")
        self.assertTrue(u.password.startswith("g5pbkdf2$sha256:12000:"))
        self.assertEqual((u.first_name, u.email), ("가상하나", ""))
        self.assertEqual(u.date_joined, datetime(2023, 3, 1, 10, 0, tzinfo=KST))
        self.assertEqual(authenticate(username="teach_one", password=PASSWORD), u)
        self.assertFalse(users.get(username="nohash_10").has_usable_password())

    def test_seats_groups_and_managers(self):
        a = self.space(A)
        seats = {m.user.username: m for m in Member.objects.in_space(a).select_related("user")}
        self.assertEqual(set(seats), {"teach_one", "teach_two", "office_3", "nurse_5", "nogroup_11"})
        self.assertEqual((seats["teach_one"].group, seats["teach_one"].cls, seats["teach_one"].name), ("g4", "4-1", "가상하나"))
        self.assertEqual(seats["teach_two"].name, "가상하나(2)")
        self.assertEqual((seats["office_3"].group, seats["office_3"].cls, seats["office_3"].position), ("office", "교무실", ""))
        self.assertEqual((seats["nurse_5"].group, seats["nurse_5"].position), ("support", "보건실 · 보건교사"))
        self.assertEqual((seats["nogroup_11"].group, seats["nogroup_11"].position), ("support", ""))
        self.assertEqual([m.user.username for m in seats.values() if m.is_manager], ["teach_one"])
        self.assertEqual(seats["teach_one"].joined_at, datetime(2023, 3, 1, 10, 0, tzinfo=KST))
        b = Member.objects.in_space(self.space(B)).get()
        self.assertTrue(b.is_manager)  # go_admin 없음 → 첫 가입자
        self.assertEqual((b.group, b.cls), ("g1", "1-2"))

    def test_scopes_channels_and_drops(self):
        self.assertEqual(self.event(100).scope, "all")
        self.assertEqual(self.event(102).scope, "me")
        self.assertEqual(self.event(102).by, self.seat("teach_one"))
        self.assertEqual(self.event(104).scope, "g4")
        self.assertEqual(self.event(105).scope, "g3")  # 옛 학년 코드
        self.assertEqual(self.event(106).scope, "office")  # 모르는 그룹 → 작성자 소속
        e = self.event(107)
        self.assertEqual((e.scope, e.title), ("support", "[특별실] 과학실 예약"))
        self.assertEqual(self.event(112).scope, "me")  # 읽기 제한
        for wr_id in (103, 111, 113):
            self.assertFalse(LegacyMap.objects.filter(kind="event", legacy_id=str(wr_id)).exists())
        self.assertEqual(Event.objects.count(), 8)  # 100(101 합침)·102·104~107·112 + B 200

    def test_body_dates_and_created_at_preserved(self):
        e = self.event(100)
        self.assertEqual((e.date, e.end, e.title), (date(2026, 5, 1), date(2026, 5, 2), "운동회"))
        self.assertEqual(e.body, "첫 줄\n둘\n셋\n[그림]")
        at = datetime(2026, 4, 1, 9, 0, tzinfo=KST)
        self.assertEqual((e.created_at, e.updated_at), (at, at))

    def test_copy_duplicates_merge_files_and_comments(self):
        e = self.event(100)
        self.assertEqual(self.event(101), e)
        self.assertEqual(sorted(e.files.filter(comment=None).values_list("name", "kind")), [("문서.hwpx", "hwpx"), ("사진.jpg", "jpg")])
        c = e.comments.get()
        self.assertEqual((c.body, c.by, c.at), ("댓글 <- 화살표", self.seat("office_3"), datetime(2026, 4, 3, 10, 0, tzinfo=KST)))
        self.assertEqual(list(c.files.order_by("pk").values_list("name", "kind", "event")),
                         [("회의록.hwpx", "hwpx", e.pk), ("가.pdf", "pdf", e.pk), ("다.pdf", "pdf", e.pk)])  # 3개까지
        self.assertEqual(c.files.first().at, datetime(2026, 4, 1, 9, 30, tzinfo=KST))
        f = e.files.get(kind="jpg")
        self.assertEqual(Path(f.file.path).read_bytes(), (TESTDATA / "real.jpg").read_bytes())

    def test_duty_kind_and_time_only(self):
        rows = list(Attendance.objects.in_space(self.space(A)).order_by("date", "kind").values_list("date", "name", "kind", "time_text"))
        self.assertEqual(rows, [(date(2026, 5, 1), "가상셋", "휴가", "종일"),
                                *[(date(2026, 5, d), "가상하나", "출장", "08:30~16:30") for d in (4, 5, 6)],
                                (date(2026, 5, 7), "가상셋", "출장", "14:00~16:30"),
                                (date(2026, 5, 8), "가상하나", "출장", "종일"),
                                (date(2026, 5, 9), "가상열둘", "연가", "종일")])
        self.assertFalse(Attendance.objects.filter(name__contains="가상장소").exists())
        self.assertEqual(Attendance.objects.filter(name="가상하나").first().member, self.seat("teach_one"))  # 명부에 이름이 하나면 이어짐
        self.assertEqual(set(Attendance.objects.filter(name="가상셋").values_list("member", flat=True)), {self.seat("office_3").pk})

    def test_isolation_every_row_in_its_legacy_school(self):
        a, b = self.space(A), self.space(B)
        self.assertEqual(list(Event.objects.in_space(b).values_list("title", flat=True)), ["나상초 행사"])
        self.assertFalse(Event.objects.in_space(a).filter(title="나상초 행사").exists())
        self.assertEqual(set(Member.objects.values_list("space", flat=True)), {a.pk, b.pk})
        self.assertFalse(Member.objects.filter(user__username__in=["cteach_9", "bteach_8"], space=a).exists())
        self.assertEqual(set(Attendance.objects.values_list("space", flat=True)), {a.pk})
        self.assertEqual(set(Notice.objects.values_list("space", flat=True)), {a.pk})

    def test_me_event_seen_only_by_writer_even_manager(self):
        mine = self.event(102)
        other = self.seat("office_3")
        other.is_manager = True
        self.assertFalse(Event.objects.in_space(self.space(A)).filter(reachable_q(other), pk=mine.pk).exists())
        self.assertTrue(Event.objects.in_space(self.space(A)).filter(reachable_q(self.seat("teach_one")), pk=mine.pk).exists())
        nurse = self.seat("nurse_5")
        self.assertFalse(Event.objects.filter(reachable_q(nurse), pk=self.event(104).pk).exists())  # 다른 소속 일정

    def test_board_to_notice(self):
        n = Notice.objects.in_space(self.space(A)).get()
        self.assertEqual((n.title, n.by, n.by_name, n.group, n.audience), ("가상 안내", self.seat("teach_one"), "가상하나", "g4", "all"))
        v = n.versions.get()
        self.assertEqual((v.v, v.body, v.fields["첨부"]), (1, "안내 본문", "안내.docx"))
        at = datetime(2025, 3, 2, 8, 0, tzinfo=KST)
        self.assertEqual((n.created_at, v.at, n.files.get().at), (at, at, at))

    def test_report_counts_and_no_personal_data(self):
        text = self.report.read_text(encoding="utf-8-sig")
        items = (self.dir / "report_items.csv").read_text(encoding="utf-8-sig")
        self.assertIn(f"{A},가상초,일정,합침,1", text)
        self.assertIn(f"{A},가상초,첨부(일정),버림: 디스크에 없음,1", text)
        self.assertIn("버림: 받지 않는 형식(zip)", items)
        self.assertIn("버림: 시간외·초과근무", items)
        self.assertIn("첨부(댓글),goteacher:300:4,버림: 댓글당 첨부 3개 넘음", items)
        self.assertIn("첨부(댓글),goteacher:300:2,버림: 받지 않는 형식(zip)", items)
        self.assertIn("첨부(댓글),goteacher:301:0,버림: 복무 댓글", items)
        self.assertIn("버림: 게시판 댓글", items)
        self.assertIn("복무,143,버림: 복무 이름 판별 못 함", items)
        self.assertIn(",일정,144,버림: 학교 코드 없음", items)
        self.assertIn("첨부(일정),goteacher:104:0,버림: 첨부 경로가 덤프 밖", items)
        self.assertIn("첨부(일정),goteacher:105:5,버림: 일정당 첨부 5개 넘음", items)
        self.assertEqual(self.event(105).files.count(), 5)
        self.assertIn("버림: 시스템·운영 계정 2", self.out)  # 계정
        self.assertIn(f"{A},자리,13,버림: 시스템·운영 계정", items)
        self.assertIn(",계정,15,버림: 아이디 규칙 밖(멈춤)", items)
        self.assertFalse(get_user_model().objects.filter(username__in=["root_13", "neis_sync_bot", "google:x1"]).exists())
        for secret in ("가상하나", "teach_one", "가상사유", "운동회", g5(PASSWORD)):
            self.assertNotIn(secret, text + items + self.out)
        self.assertIn("일정: 원본 13 · 넣음 8 · 이미 넣음 0 · 합침 1 · 버림 4", self.out)

    def test_rerun_adds_nothing_and_increment_adds_only_new(self):
        before = {m: m.objects.count() for m in (get_user_model(), Member, Event, Attendance, Notice, LegacyMap)}
        files = sorted(p for p in self.media.rglob("*") if p.is_file())
        out = self.run_import()
        self.assertEqual({m: m.objects.count() for m in before}, before)
        self.assertEqual(sorted(p for p in self.media.rglob("*") if p.is_file()), files)
        self.assertIn("일정: 원본 13 · 넣음 0 · 이미 넣음 9 · 합침 0 · 버림 4", out)

        self.data["g5_write_goteacher"] += [write(114, A, 0, "새 일정", "teach_one"),
                                            write(115, A, 1, "운동회", "teach_one", "2026-05-01", "2026-05-02")]
        self.data["g5_member"].append(member(12, "new_12", "가상새", A, 10, "2반"))
        dump_to(self.dir, self.data)
        self.run_import()
        self.assertEqual(Event.objects.count(), before[Event] + 1)
        self.assertEqual(self.event(115), self.event(100))  # 새 복사본은 이미 넣은 일정으로 합침
        self.assertEqual(self.seat("new_12").cls, "4-2")


class StopTests(ImportBase):
    def test_failure_in_later_school_leaves_no_orphan_files(self):
        real = Importer._notices

        def boom(self, code, space, seats):
            if code == B:
                raise RuntimeError("가상 실패")
            return real(self, code, space, seats)

        with mock.patch.object(Importer, "_notices", boom), self.assertRaises(RuntimeError):
            self.run_import()
        self.assertEqual([p for p in self.media.rglob("*") if p.is_file()], [])
        self.assertEqual((get_user_model().objects.count(), LegacyMap.objects.count()), (0, 0))

    def test_size_and_quota_limits(self):
        with mock.patch("legacy.importer.MAX_FILE", 100):
            out = self.run_import("--school", B, A)
        self.assertIn("버림: 0MB 넘음", out)  # 가상 jpg·hwpx는 100바이트보다 크다

    def test_school_quota(self):
        with mock.patch("legacy.importer.storage_room", return_value=(10, "Quota")):
            out = self.run_import()
        self.assertIn("버림: 저장 한도 넘음", out)
        self.assertFalse(Event.objects.filter(files__isnull=False).exists())

    def test_existing_username_is_not_overwritten(self):
        u = get_user_model().objects.create_user("TEACH_ONE", password="다른비밀번호9")
        out = self.run_import()
        u.refresh_from_db()
        self.assertTrue(u.check_password("다른비밀번호9"))
        self.assertFalse(Member.objects.filter(user=u).exists())
        self.assertIn("버림: 같은 아이디가 이미 있음(멈춤) 1", out)
        self.assertIn("버림: 계정을 만들지 못함(아이디 겹침·규칙 밖) 2", out)
        self.assertEqual(self.event(100).by, None)  # 자리 없는 작성자의 학교 일정은 by 없이
        self.assertFalse(LegacyMap.objects.filter(kind="event", legacy_id="102").exists())  # '나' 일정은 버림

    def test_school_with_existing_space_stops(self):
        s = School.objects.create(neis_atpt="E10", neis_code=A, name="가상초")
        Space.objects.create(school=s, kind="grade", grade=4, name="4학년")
        out = self.run_import()
        self.assertIn("버림: 이미 공간이 있는 학교(멈춤) 1", out)
        self.assertFalse(Space.objects.filter(school=s, grade=0).exists())
        self.assertFalse(Event.objects.filter(space__school=s).exists())
        s.refresh_from_db()
        self.assertEqual(s.plan, "free")
        self.assertTrue(Space.objects.filter(school__neis_code=B, grade=0).exists())  # 다른 학교는 그대로 진행

    def test_school_option_and_dry_run(self):
        out = self.run_import("--school", B, "--dry-run")
        self.assertIn("마른 실행", out)
        self.assertEqual((get_user_model().objects.count(), School.objects.count(), LegacyMap.objects.count()), (0, 0, 0))
        self.assertEqual([p for p in self.media.rglob("*") if p.is_file()], [])
        self.run_import("--school", B)
        self.assertEqual(list(School.objects.values_list("neis_code", flat=True)), [B])

