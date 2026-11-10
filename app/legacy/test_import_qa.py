"""P3 이관 도구 — QA가 더한 경계 사례(구현 테스트 legacy/tests.py와 겹치지 않는 것만). 픽스처는 모두 가상 값.

- 범위(35번 5절 3): 이관된 '나' 일정·그 첨부·댓글 첨부는 같은 학교 관리자에게도 실제 화면 주소에서 404, 일정 화면 목록에도 없다.
- 덤프 안에서 대소문자만 다른 두 아이디: 뒤의 것은 계정·자리를 만들지 않고 멈춘다(덮어쓰기·로그인 섞임 없음).
- 다른 학교 회원이 쓴 댓글·게시글: 이 학교 자리와 잇지 않는다(이름 글자만).
- 증분: 이미 넣은 일정에 새 댓글·새 첨부, 새 복무·새 게시글이 더해지고 그 밖은 늘지 않는다.
"""
from django.contrib.auth import get_user_model
from django.test import Client

from notices.models import Notice
from spaces.models import Attendance, Member
from today.models import EventComment, EventFile

from .models import LegacyMap
from .tests import A, B, PDF, ImportBase, bf, dump_to, member, write


class ViewScopeTests(ImportBase):
    def setUp(self):
        super().setUp()
        self.data["g5_board_file"] += [bf("goteacher", 102, 1, "개인.pdf", "p1", PDF),
                                       bf("goteacher", 310, 0, "개인댓글.pdf", "p2", PDF)]
        self.data["g5_write_goteacher"].append(
            write(310, A, 0, "", "teach_one", content="내 메모 댓글", parent=102, at="2026-04-05 10:00:00"))
        dump_to(self.dir, self.data)
        self.run_import()

    def test_manager_gets_404_for_writers_me_event_its_files_and_comment_files(self):
        e = self.event(102)
        own, cfile = e.files.get(comment=None), e.files.exclude(comment=None).get()
        mgr = self.seat("office_3")
        Member.objects.filter(pk=mgr.pk).update(is_manager=True)
        base = f"/s/{e.space.token}/today/e/{e.key}"
        c = Client()
        c.force_login(mgr.user)
        for tail in ("/more", f"/f/{own.key}", f"/f/{cfile.key}"):
            self.assertEqual(c.get(base + tail).status_code, 404, tail)
        week = c.get(f"/s/{e.space.token}/schedule?w=2026-05-01")
        self.assertEqual(week.status_code, 200)
        self.assertNotIn(e.key, week.content.decode())
        c.force_login(self.seat("teach_one").user)
        self.assertContains(c.get(base + "/more"), "내 메모 댓글")
        self.assertEqual(c.get(base + f"/f/{cfile.key}").status_code, 200)

    def test_group_event_404_for_other_group_member(self):
        e = self.event(104)  # g4 소속 일정
        c = Client()
        c.force_login(self.seat("nurse_5").user)
        self.assertEqual(c.get(f"/s/{e.space.token}/today/e/{e.key}/more").status_code, 404)
        c.force_login(self.seat("teach_two").user)  # 같은 g4
        self.assertEqual(c.get(f"/s/{e.space.token}/today/e/{e.key}/more").status_code, 200)

    def test_other_school_member_cannot_open_by_swapping_space_token(self):
        e = self.event(100)
        b_space = self.space(B)
        c = Client()
        c.force_login(self.seat("bteach_8").user)
        self.assertEqual(c.get(f"/s/{b_space.token}/today/e/{e.key}/more").status_code, 404)
        self.assertNotEqual(c.get(f"/s/{e.space.token}/today/e/{e.key}/more").status_code, 200)


class CaseCollisionInDumpTests(ImportBase):
    def test_second_id_differing_only_by_case_stops(self):
        self.data["g5_member"].append(member(13, "TEACH_one", "가상겹침", A, 10, "3반"))
        dump_to(self.dir, self.data)
        out = self.run_import()
        users = get_user_model().objects
        self.assertEqual(users.filter(username__iexact="teach_one").count(), 1)
        self.assertEqual(users.get(username__iexact="teach_one").username, "teach_one")  # 먼저 나온 것
        self.assertFalse(Member.objects.filter(name="가상겹침").exists())
        self.assertFalse(LegacyMap.objects.filter(kind="user", legacy_id="13").exists())
        self.assertIn("버림: 같은 아이디가 이미 있음(멈춤) 1", out)
        out2 = self.run_import()  # 다시 돌려도 같은 까닭으로 멈춘다
        self.assertIn("버림: 같은 아이디가 이미 있음(멈춤) 1", out2)
        self.assertEqual(users.filter(username__iexact="teach_one").count(), 1)


class CrossSchoolAuthorTests(ImportBase):
    def test_comment_and_post_by_other_school_member_are_not_linked_to_a_seat(self):
        self.data["g5_write_goteacher"].append(
            write(320, A, 0, "", "bteach_8", content="다른 학교 댓글", parent=100, wr_name="가상외부"))
        self.data["g5_write_free_school"].append(write(402, A, "", "외부 글", "bteach_8", "", "", "본문", wr_name="가상외부"))
        dump_to(self.dir, self.data)
        self.run_import()
        c = EventComment.objects.get(pk=LegacyMap.objects.get(kind="comment", legacy_id="320").obj_id)
        self.assertEqual((c.by, c.by_name), (None, "가상외부"))
        self.assertEqual(c.event.space, self.space(A))
        n = Notice.objects.get(pk=LegacyMap.objects.get(kind="notice", legacy_id="402").obj_id)
        self.assertEqual((n.by, n.by_name, n.group, n.space), (None, "가상외부", "", self.space(A)))


class IncrementTests(ImportBase):
    def test_increment_adds_new_comment_file_duty_and_post_only(self):
        self.run_import()
        before = {m: m.objects.count() for m in (EventComment, EventFile, Attendance, Notice, Member)}
        self.data["g5_write_goteacher"] += [
            write(330, A, 0, "", "teach_one", content="새 댓글", parent=100, at="2026-06-01 10:00:00"),
            write(117, A, 2, "가상셋 | 연가", "office_3", "2026-06-02"),
        ]
        self.data["g5_board_file"].append(bf("goteacher", 100, 2, "새.pdf", "n1", PDF))
        self.data["g5_write_free_school"].append(write(403, A, "", "새 안내", "teach_one", "", "", "새 본문"))
        dump_to(self.dir, self.data)
        out = self.run_import()
        after = {m: m.objects.count() for m in before}
        self.assertEqual(after, {**before, EventComment: before[EventComment] + 1, EventFile: before[EventFile] + 1,
                                 Attendance: before[Attendance] + 1, Notice: before[Notice] + 1})
        self.assertEqual(self.event(100).files.filter(name="새.pdf").count(), 1)
        self.assertIn("댓글: 원본 3 · 넣음 1 · 이미 넣음 1", out)
        third = self.run_import()
        self.assertEqual({m: m.objects.count() for m in before}, after)
        self.assertIn("일정: 원본 13 · 넣음 0", third)  # 학교 코드 없는 행 1 포함
