"""V2 구 → 새 동기화(legacy_sync). 픽스처는 tests.py의 가상 데이터.

가짜 원천: sqlite 메모리 DB에 구 표를 그대로 만들고 운영 코드의 SQL을 그대로 돌린다(CRC32만 함수로 등록).
그래서 '구 DB로 SELECT 외 쿼리 0'을 실제로 나간 문장으로 확인한다. 실제 MySQL 문법은 이름 붙인 컨테이너로 따로 확인했다.

불변식
- 구에서 쓰기·고치기·지우기 → 새 쪽에 반영. 같은 상태로 다시 돌리면 변화 0.
- 같은 변경을 동기화로 반영한 결과 = 바뀐 덤프로 처음부터 전체 이관한 결과.
- 이관 학교 밖 행은 읽지도 않는다. 로그에는 학교 코드·종류·구 번호·까닭만.
"""
import io
import re
import sqlite3
import zlib
from datetime import date
from pathlib import Path
from unittest import mock

from django.contrib.auth import authenticate, get_user, get_user_model
from django.core.management import call_command
from django.http import HttpRequest
from django.test import override_settings

from accounts.models import DeviceToken

from notices.models import Notice
from spaces.models import Attendance, Member, School, Space
from today.models import Event, EventComment, EventFile

from . import importer
from .models import LegacyMap, LegacyPrint
from .sync import MySQL, Sync
from .tests import PASSWORD, PDF, A, B, C, ImportBase, bf, dump_to, g5, member, write

COLUMNS = {
    "g5_member": list(member(1, "x", "x", A, 1)),
    "g5_write_goteacher": list(write(1, A, 0, "x", "x")),
    "g5_write_free_school": list(write(1, A, 0, "x", "x")),
    "g5_board_file": [k for k in bf("goteacher", 1, 0, "x", "x") if k != "_data"],
    "contact_group": ["group_id", "group_name", "school_id", "sort_order", "is_active", "created_at", "updated_at"],
    "school": ["id", "code_sido", "name", "grade"],
    "g5_write_channel": ["wr_id", "wr_3", "wr_subject"],
    "g5_write_go_admin": ["wr_id", "wr_3", "mb_id", "wr_is_comment"],
}


class FakeLegacy(MySQL):
    """부를 때마다 지금 data로 sqlite 표를 다시 만든다(= 구 DB의 현재 상태). sql: 나간 문장 전부."""

    def __init__(self, data, files):
        super().__init__(None, files)
        self.data, self.sql = data, []

    def _run(self, sql, args):
        self.sql.append(sql)
        db = sqlite3.connect(":memory:")
        db.create_function("CRC32", 1, lambda s: None if s is None else zlib.crc32(str(s).encode()))
        for table, cols in COLUMNS.items():
            db.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
            db.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})",
                           [[r[c] for c in cols] for r in self.data.get(table, [])])
        cur = db.execute(sql.replace("%s", "?"), list(args))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


class SyncBase(ImportBase):
    def setUp(self):
        super().setUp()
        self.run_import()
        self.logs = []
        self.legacy = FakeLegacy(self.data, self.dir / "files")
        self.s = Sync(self.legacy, self.logs.append)
        self.s.full()  # 첫 실행: 이관 기록과 맞추고 지문을 기억

    def sync(self, step=False):
        dump_to(self.dir, self.data)  # 새 첨부 파일을 구 첨부 폴더에
        return self.s.step() if step else self.s.full()

    def rows(self, table):
        return self.data[table]

    def row(self, table, **eq):
        return next(r for r in self.data[table] if all(r[k] == v for k, v in eq.items()))

    def counts(self):
        return {m: m.objects.count() for m in (get_user_model(), Member, Event, EventComment, EventFile, Attendance, Notice,
                                                LegacyMap)}


class FirstRunTests(SyncBase):
    def test_first_run_changes_nothing_and_rerun_is_zero(self):
        before = self.counts()
        self.assertEqual(self.s.full(), {"changed": 0, "applied": 0, "failed": 0})
        self.assertEqual(self.counts(), before)
        self.assertTrue(LegacyPrint.objects.filter(kind="goteacher", legacy_id="100", school=A).exists())
        self.assertEqual(Sync(self.legacy, self.logs.append).full()["changed"], 0)  # 다시 띄워도(지문은 DB에) 0

    def test_first_run_removes_rows_deleted_since_dump(self):
        LegacyPrint.objects.all().delete()
        self.data["g5_write_goteacher"].remove(self.row("g5_write_goteacher", wr_id=104))
        Sync(self.legacy, self.logs.append).full()
        self.assertFalse(LegacyMap.objects.filter(kind="event", legacy_id="104").exists())
        self.assertFalse(Event.objects.filter(title="그룹 일정").exists())

    def test_outside_schools_are_not_read(self):
        self.data["g5_write_goteacher"].append(write(900, C, 0, "다상초 행사", "cteach_9"))
        self.data["g5_member"].append(member(30, "c_new_30", "가상서른", C, 30, "3반"))
        self.assertEqual(self.sync()["changed"], 0)
        self.assertFalse(Event.objects.filter(title="다상초 행사").exists())
        self.assertFalse(LegacyPrint.objects.exclude(school__in=[A, B]).exists())

    def test_only_select_reaches_legacy_db(self):
        self.data["g5_write_goteacher"].append(write(120, A, 0, "새 일정", "teach_one"))
        self.sync(step=True)
        self.sync()
        self.assertTrue(self.legacy.sql)
        self.assertEqual([q for q in self.legacy.sql if not re.match(r"\s*SELECT\s", q)], [])
        with self.assertRaises(ValueError):
            MySQL(None, "").select("UPDATE g5_member SET mb_name = 'x'")
        for bad in ("SELECT 1; DELETE FROM g5_member", "SELECT * FROM g5_member INTO OUTFILE '/tmp/x'",
                    "SELECT * FROM g5_member FOR UPDATE", "SELECT * FROM g5_member LOCK IN SHARE MODE",
                    "SELECT * FROM g5_member FOR SHARE", " select @a := 1 into @b"):
            with self.assertRaises(ValueError):
                MySQL(None, "").select(bad)


class EventSyncTests(SyncBase):
    def test_new_event_comes_in_increment(self):
        self.data["g5_write_goteacher"].append(write(600, A, 0, "새 일정", "teach_one", "2026-06-01"))  # 새 글은 번호가 크다
        self.assertEqual(self.sync(step=True)["applied"], 1)
        e = self.event(600)
        self.assertEqual((e.space, e.title, e.date, e.scope, e.by), (self.space(A), "새 일정", date(2026, 6, 1), "all", self.seat("teach_one")))
        self.assertEqual(self.sync()["changed"], 0)  # 멱등

    def test_edit_waits_for_full_compare_and_keeps_pk_comments(self):
        self.row("g5_write_goteacher", wr_id=104).update(wr_subject="고친 그룹 일정", wr_1="2026-06-02", wr_2="2026-06-04")
        before = self.event(104)
        self.assertEqual(self.sync(step=True)["changed"], 0)  # 그누보드는 고쳐도 번호·시각이 그대로 → 증분은 못 봄
        self.assertEqual(self.sync()["applied"], 1)
        e = self.event(104)
        self.assertEqual((e.pk, e.title, e.date, e.end, e.scope), (before.pk, "고친 그룹 일정", date(2026, 6, 2), date(2026, 6, 4), "g4"))
        self.assertEqual(self.sync()["changed"], 0)

    def test_edit_one_of_merged_copies_splits_it(self):
        self.row("g5_write_goteacher", wr_id=101).update(wr_subject="운동회 예비일", wr_1="2026-05-08", wr_2="")
        self.sync()
        a, b = self.event(100), self.event(101)
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual((a.title, b.title, b.date, b.end), ("운동회", "운동회 예비일", date(2026, 5, 8), None))
        self.assertEqual(b.comments.get().body, "댓글 <- 화살표")  # 101의 댓글은 101을 따라간다
        self.assertEqual(sorted(a.files.values_list("name", flat=True)), ["문서.hwpx"])
        self.assertEqual(sorted(b.files.filter(comment=None).values_list("name", flat=True)), ["사진.jpg"])

    def test_delete_event_takes_comments_and_files(self):
        self.data["g5_write_goteacher"] = [r for r in self.rows("g5_write_goteacher") if r["wr_id"] not in (100, 101, 300)]
        self.data["g5_board_file"] = [f for f in self.rows("g5_board_file") if f["wr_id"] not in (100, 101, 300)]
        files = [Path(f.file.path) for f in EventFile.objects.filter(event__title="운동회")]
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertFalse(Event.objects.filter(title="운동회").exists())
        self.assertFalse(EventComment.objects.exists())
        self.assertFalse(LegacyMap.objects.filter(kind__in=["event", "comment"], legacy_id__in=["100", "101", "300"]).exists())
        self.assertFalse(any(p.exists() for p in files))  # 디스크 파일도(커밋 뒤)

    def test_duty_range_edit_and_delete(self):
        self.row("g5_write_goteacher", wr_id=108).update(wr_2="2026-05-05")
        self.sync()
        days = list(Attendance.objects.filter(name="가상하나", kind="출장", time_text="08:30~16:30").values_list("date", flat=True))
        self.assertEqual(sorted(days), [date(2026, 5, 4), date(2026, 5, 5)])
        self.data["g5_write_goteacher"].remove(self.row("g5_write_goteacher", wr_id=108))
        self.data["g5_write_goteacher"].append(write(117, A, 2, "가상셋 | 조퇴 14:00~16:30", "office_3", "2026-06-03"))
        self.sync()
        self.assertFalse(Attendance.objects.filter(time_text="08:30~16:30").exists())
        self.assertTrue(Attendance.objects.filter(date=date(2026, 6, 3), name="가상셋", kind="조퇴").exists())

    def test_comment_and_attachments_add_delete(self):
        self.data["g5_write_goteacher"].append(write(302, A, 0, "", "teach_one", content="새 댓글", parent=104, at="2026-04-05 10:00:00"))
        self.data["g5_board_file"] += [bf("goteacher", 302, 0, "댓글첨부.pdf", "n1", PDF), bf("goteacher", 107, 0, "예약표.pdf", "n2", PDF)]
        self.sync()
        e = self.event(104)
        c = e.comments.get()
        self.assertEqual((c.body, c.by), ("새 댓글", self.seat("teach_one")))
        self.assertEqual(list(c.files.values_list("name", flat=True)), ["댓글첨부.pdf"])
        self.assertEqual(list(self.event(107).files.values_list("name", flat=True)), ["예약표.pdf"])

        self.data["g5_write_goteacher"].remove(self.row("g5_write_goteacher", wr_id=302))
        self.data["g5_board_file"] = [f for f in self.rows("g5_board_file") if f["bf_file"] not in ("n1", "n2", "f2")]
        self.sync()
        self.assertFalse(e.comments.exists())
        self.assertFalse(self.event(107).files.exists())
        self.assertEqual(list(self.event(100).files.filter(comment=None).values_list("name", flat=True)), ["사진.jpg"])  # 문서.hwpx 지움
        self.assertEqual(self.sync()["changed"], 0)


class BoardSyncTests(SyncBase):
    def test_post_write_edit_delete_and_file_on_old_post(self):
        self.data["g5_write_free_school"].append(write(402, A, "", "새 안내", "office_3", "", "", "새 본문"))
        self.sync(step=True)
        n = Notice.objects.get(title="새 안내")
        self.assertEqual((n.space, n.by, n.versions.get().body), (self.space(A), self.seat("office_3"), "새 본문"))

        self.row("g5_write_free_school", wr_id=402).update(wr_subject="고친 안내", wr_content="고친 본문")
        self.data["g5_board_file"].append(bf("free_school", 400, 1, "추가.pdf", "n3", PDF))
        self.sync()
        n = Notice.objects.get(title="고친 안내")
        self.assertEqual(n.versions.get().body, "고친 본문")
        old = Notice.objects.get(title="가상 안내")
        self.assertEqual(sorted(old.files.values_list("name", flat=True)), ["안내.docx", "추가.pdf"])
        self.assertEqual(old.versions.get().fields["첨부"], "안내.docx, 추가.pdf")

        self.data["g5_write_free_school"].remove(self.row("g5_write_free_school", wr_id=402))
        self.sync()
        self.assertFalse(Notice.objects.filter(title="고친 안내").exists())
        self.assertEqual(self.sync()["changed"], 0)


class MemberSyncTests(SyncBase):
    def test_new_member_gets_account_and_seat(self):
        self.data["g5_member"].append(member(16, "new_16", "가상열여섯", A, 10, "3반", joined="2026-11-16 08:00:00"))
        self.sync(step=True)
        seat = self.seat("new_16")
        self.assertEqual((seat.space, seat.cls, seat.group, seat.is_manager), (self.space(A), "4-3", "g4", False))
        self.assertEqual(authenticate(username="new_16", password=PASSWORD), seat.user)

    def test_group_class_and_password_change(self):
        self.row("g5_member", mb_no=3).update(mb_3="10", mb_4="2반")
        self.row("g5_member", mb_no=5).update(mb_password=g5("새비밀번호456", salt="다른솔트"))
        self.sync()
        s = self.seat("office_3")
        self.assertEqual((s.group, s.cls, s.position), ("g4", "4-2", ""))
        self.assertIsNone(authenticate(username="nurse_5", password=PASSWORD))
        self.assertEqual(authenticate(username="nurse_5", password="새비밀번호456").username, "nurse_5")

    def test_leave_block_and_move_out(self):
        DeviceToken.issue(get_user_model().objects.get(username="nogroup_11"))
        self.row("g5_member", mb_no=11).update(mb_leave_date="20261116")
        self.row("g5_member", mb_no=5).update(mb_3="12")  # 전출 그룹
        self.sync()
        self.assertFalse(Member.objects.filter(user__username__in=["nogroup_11", "nurse_5"]).exists())
        self.assertFalse(get_user_model().objects.get(username="nogroup_11").is_active)
        self.assertIsNone(authenticate(username="nogroup_11", password=PASSWORD))
        self.assertFalse(DeviceToken.objects.filter(user__username="nogroup_11").exists())
        self.assertTrue(get_user_model().objects.get(username="nurse_5").is_active)  # 전출은 계정 그대로
        self.assertEqual(self.sync()["changed"], 0)

    def test_transfer_drops_old_seat_and_seats_in_new_target_school(self):
        self.row("g5_member", mb_no=2).update(mb_1=B, mb_3="20", mb_4="3반", mb_5="0")  # 이관 학교 B로, 아직 가입 승인 전
        self.row("g5_member", mb_no=5).update(mb_1=C, mb_3="30", mb_4="1반")  # 이관 대상 밖 학교로
        self.sync()
        self.assertFalse(Member.objects.filter(user__username__in=["teach_two", "nurse_5"]).exists())  # 옛 학교 자리 지움
        self.assertTrue(get_user_model().objects.get(username="nurse_5").is_active)  # 계정은 그대로
        self.assertEqual(self.sync()["changed"], 0)
        self.row("g5_member", mb_no=2).update(mb_5="1")  # B에서 승인
        self.sync()
        moved = self.seat("teach_two")
        self.assertEqual((moved.space, moved.cls, moved.name), (self.space(B), "1-3", "가상하나"))
        self.assertTrue(get_user_model().objects.get(username="teach_two").is_active)
        self.assertEqual(self.sync()["changed"], 0)

    def test_mb5_off_keeps_seat(self):  # 구에서 학교 코드를 바꾸면 전원 mb_5=0
        self.row("g5_member", mb_no=1).update(mb_5="0")
        self.row("g5_member", mb_no=3).update(mb_5="0", mb_4="교무부장")
        self.sync()
        self.assertEqual(self.seat("teach_one").cls, "4-1")
        self.assertEqual(self.seat("office_3").position, "교무부장")

    def test_deleted_row_is_like_leave_and_active_never_raised(self):
        self.data["g5_member"].remove(self.row("g5_member", mb_no=3))
        self.row("g5_member", mb_no=1).update(mb_intercept_date="20261116")
        self.sync()
        users = get_user_model().objects
        self.assertFalse(users.get(username="office_3").is_active)
        self.assertFalse(Member.objects.filter(user__username="office_3").exists())
        self.assertFalse(users.get(username="teach_one").is_active)
        self.row("g5_member", mb_no=1).update(mb_intercept_date="")  # 차단 풀림: 동기화가 다시 열지 않는다
        self.sync()
        self.assertFalse(users.get(username="teach_one").is_active)

    def test_password_only_when_legacy_password_changes(self):
        u = get_user_model().objects.get(username="nurse_5")
        u.set_password("새쪽비밀번호789")  # 새 쪽에서 바꾼(또는 관리자가 발급한 임시) 비밀번호
        u.save()
        self.row("g5_member", mb_no=5).update(mb_name="가상다섯고침", mb_4="보건")
        self.sync()
        u.refresh_from_db()
        self.assertTrue(u.check_password("새쪽비밀번호789"))
        self.assertEqual(u.first_name, "가상다섯고침")

        self.client.force_login(u)
        DeviceToken.issue(u)
        self.row("g5_member", mb_no=5).update(mb_password=g5("구에서바꿈456", salt="다른솔트"))
        self.sync()
        self.assertEqual(authenticate(username="nurse_5", password="구에서바꿈456"), u)
        self.assertIsNone(authenticate(username="nurse_5", password="새쪽비밀번호789"))
        self.assertFalse(DeviceToken.objects.filter(user=u).exists())
        req = HttpRequest()
        req.session = self.client.session
        self.assertTrue(get_user(req).is_anonymous)  # 세션도 끊김(세션 인증 해시)

    def test_school_admin_follows_go_admin(self):
        self.data["g5_write_go_admin"] = [{"wr_id": 2, "wr_3": A, "mb_id": "office_3", "wr_is_comment": 0}]
        self.sync()
        managers = Member.objects.in_space(self.space(A)).filter(is_manager=True)
        self.assertEqual([m.user.username for m in managers], ["office_3"])

    def test_go_admin_diff_keeps_managers_set_on_new_side(self):
        Member.objects.filter(pk=self.seat("nurse_5").pk).update(is_manager=True)  # 새 쪽에서 지정
        self.data["g5_write_go_admin"].append({"wr_id": 3, "wr_3": A, "mb_id": "office_3", "wr_is_comment": 0})
        self.sync()
        self.assertEqual({m.user.username for m in Member.objects.in_space(self.space(A)).filter(is_manager=True)},
                         {"teach_one", "office_3", "nurse_5"})
        self.data["g5_write_go_admin"] = [a for a in self.data["g5_write_go_admin"] if a["mb_id"] != "teach_one"]
        self.sync()
        self.assertEqual({m.user.username for m in Member.objects.in_space(self.space(A)).filter(is_manager=True)},
                         {"office_3", "nurse_5"})


class SameAsFullImportTests(SyncBase):
    def change_everything(self):
        d = self.data
        d["g5_write_goteacher"] += [write(120, A, 0, "새 일정", "teach_one", "2026-06-01"),
                                    write(121, B, 0, "나상초 새 행사", "bteach_8"),
                                    write(117, A, 2, "가상셋 | 조퇴 14:00~16:30", "office_3", "2026-06-03"),
                                    write(302, A, 0, "", "teach_one", content="새 댓글", parent=104, at="2026-04-05 10:00:00")]
        self.row("g5_write_goteacher", wr_id=104).update(wr_subject="고친 그룹 일정")
        self.row("g5_write_goteacher", wr_id=100).update(wr_1="2026-05-02")  # 합친 복사본의 주인을 고침 → 둘로
        self.row("g5_write_goteacher", wr_id=108).update(wr_2="2026-05-05")
        d["g5_write_goteacher"] = [r for r in d["g5_write_goteacher"] if r["wr_id"] not in (102, 109)]
        d["g5_board_file"] = [f for f in d["g5_board_file"] if f["bf_file"] != "f2"]
        d["g5_board_file"] += [bf("goteacher", 107, 0, "예약표.pdf", "n2", PDF), bf("free_school", 402, 0, "붙임.pdf", "n4", PDF)]
        self.row("g5_write_free_school", wr_id=400).update(wr_subject="고친 안내")
        d["g5_write_free_school"].append(write(402, A, "", "새 안내", "office_3", "", "", "새 본문"))
        d["g5_member"].append(member(16, "new_16", "가상열여섯", A, 10, "3반", joined="2026-11-16 08:00:00"))
        self.row("g5_member", mb_no=2).update(mb_3="11", mb_4="교무")
        d["g5_member"].append(member(17, "new_17", "가상열일곱", A, 11, "교사", joined="2026-11-16 09:00:00"))
        self.row("g5_member", mb_no=8).update(mb_1=A, mb_3="10", mb_4="2반")  # B → A 전근
        self.row("g5_member", mb_no=5).update(mb_password=g5("새비밀번호456", salt="다른솔트"))
        self.row("g5_member", mb_no=11).update(mb_leave_date="20261116")
        d["g5_write_go_admin"].append({"wr_id": 2, "wr_3": A, "mb_id": "office_3", "wr_is_comment": 0})

    def snapshot(self):
        out = {}
        for sp in Space.objects.filter(grade=0).select_related("school"):
            events = sorted((
                (e.date, e.end, e.title, e.scope, e.by.name if e.by else None, e.body,
                 sorted((c.by_name, c.body, sorted(c.files.values_list("name", flat=True))) for c in e.comments.all()),
                 sorted(e.files.filter(comment=None).values_list("name", flat=True)))
                for e in Event.objects.in_space(sp)), key=repr)
            duty = sorted(Attendance.objects.in_space(sp).values_list("date", "name", "kind", "time_text"))
            notices = sorted((n.title, n.by_name, n.versions.get().body, n.versions.get().fields["첨부"],
                              sorted(n.files.values_list("name", flat=True))) for n in Notice.objects.in_space(sp))
            seats = sorted(Member.objects.in_space(sp).values_list("user__username", "name", "group", "cls", "position", "is_manager"))
            out[sp.school.neis_code] = (events, duty, notices, seats)
        users = sorted((u, p if not p.startswith("!") else "쓸 수 없음", n)  # 쓸 수 없는 비밀번호는 매번 무작위
                       for u, p, n in get_user_model().objects.filter(is_active=True).values_list("username", "password", "first_name"))
        return out, users

    def test_sync_result_equals_fresh_full_import(self):
        self.change_everything()
        self.sync()
        self.assertEqual(self.sync()["changed"], 0)
        synced = self.snapshot()
        Space.objects.all().delete()
        School.objects.all().delete()
        get_user_model().objects.all().delete()
        LegacyMap.objects.all().delete()
        self.run_import("--school", A, B)  # 동기화는 대상 학교가 고정(B는 전근으로 활동 회원이 없어져도 대상)
        self.assertEqual(synced, self.snapshot())


class FailureTests(SyncBase):
    def test_failed_row_is_skipped_logged_and_retried(self):
        real = importer.judge_file

        def boom(path, source):
            if source == "새.pdf":
                raise RuntimeError("가상 실패 가상하나")
            return real(path, source)

        self.data["g5_write_goteacher"].append(write(122, A, 0, "같이 쓴 일정", "teach_one"))
        self.data["g5_board_file"].append(bf("goteacher", 107, 0, "새.pdf", "n5", PDF))
        with mock.patch("legacy.importer.judge_file", boom):
            r = self.sync()
        self.assertEqual((r["applied"], r["failed"]), (1, 1))
        self.assertEqual(self.event(122).title, "같이 쓴 일정")
        self.assertFalse(self.event(107).files.exists())
        self.assertIn(f"legacy_sync {A} file goteacher:107:0 실패: RuntimeError", self.logs)
        for secret in ("가상하나", "teach_one", "같이 쓴 일정", "새.pdf"):
            self.assertFalse([line for line in self.logs if secret in line])
        self.assertEqual(self.sync()["applied"], 1)  # 다음 대조에서 다시
        self.assertEqual(list(self.event(107).files.values_list("name", flat=True)), ["새.pdf"])


class CommandTests(SyncBase):
    def test_off_without_url(self):
        out = io.StringIO()
        with override_settings(LEGACY_DB_URL=""):
            call_command("legacy_sync", "--once", stdout=out)
        self.assertIn("꺼짐", out.getvalue())

    def test_once(self):
        self.data["g5_write_goteacher"].append(write(120, A, 0, "새 일정", "teach_one"))
        out = io.StringIO()
        with override_settings(LEGACY_DB_URL="mysql://ro:pw@mysql:3306/goteacher", LEGACY_FILES_DIR=str(self.dir / "files")), \
                mock.patch("legacy.sync.MySQL.connect", return_value=self.legacy):
            call_command("legacy_sync", "--once", stdout=out)
        self.assertIn("legacy_sync 대조: 바뀐 행 1 · 반영 1 · 실패 0", out.getvalue())
        self.assertTrue(Event.objects.filter(title="새 일정").exists())
