"""H4 독립 검증(gt-qa): test_school_aud.py에 없는 경계만. expectedFailure는 확인된 결함이다(고치면 표시를 지운다)."""
from datetime import date, timedelta

from django.test import Client
from django.utils import timezone

from spaces.models import Duty, Member, School, Space

from . import bell, hwpform
from .models import Cell, Collection, Notification
from .recipients import audience_rows, roster_rows
from .test_school_aud import SchoolBase


class ListQa(SchoolBase):
    def test_school_wide_search_by_sender_name(self):
        """검색 칸 안내가 '제목·보낸 사람으로 찾기'다. 내 탭처럼 학교 전체 탭도 보낸 사람으로 찾아져야 한다."""
        c = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        boss = self.login(self.boss)
        self.assertContains(boss.get(f"{self.base}/c/?tab=all&q=조사"), f"/c/{c.key}/")
        self.assertContains(boss.get(f"{self.base}/c/?tab=all&q=사일"), f"/c/{c.key}/")

    def test_member_without_group_does_not_get_normal_rows_as_group_rows(self):
        """그룹이 빈 회원: 그룹 칸이 아닌 줄(group='')이 내 것으로 잡히면 안 된다."""
        c = self.make(who=self.boss, aud="groups", aud_groups=["g4"], aud_hr="on")
        Member.objects.filter(pk=self.t51.pk).update(group="")
        self.assertNotContains(self.login(self.t51).get(f"{self.base}/c/?tab=todo"), c.link_token)

    def test_manager_cannot_again_someone_elses(self):
        c = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        n = Collection.objects.count()
        self.assertEqual(self.login(self.boss).post(f"{self.base}/c/{c.key}/again").status_code, 403)
        self.assertEqual(Collection.objects.count(), n)

    def test_again_keeps_group_rows(self):
        c = self.make(aud="pergroup", aud_pgroups=["g4", "office"])
        self.assertEqual(self.login(self.boss).post(f"{self.base}/c/{c.key}/again").status_code, 303)
        new = Collection.objects.latest("pk")
        self.assertEqual(list(new.rows.values_list("unit", "group")), [("4학년", "g4"), ("교무실", "office")])
        self.assertEqual(new.unit_head, ["그룹", "작성"])


class ExpiredAndMergedQa(SchoolBase):
    def test_expired_school_still_collects_with_range(self):
        """리드 결정: 만료돼도 기존 회원의 취합은 계속."""
        School.objects.filter(pk=self.school.pk).update(paid_until=date(2020, 1, 1))
        c = self.make(who=self.t41, aud="all")
        self.assertEqual(c.rows.count(), len(self.roster))

    def test_old_grade_address_does_not_open_others_board(self):
        """합쳐진 옛 공간 주소로 돌아가도 남의 합본은 404, 만든 사람은 열림."""
        old = Space.objects.create(school=self.school, grade=4, name="옛 4학년", merged_into=self.hub)
        c = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        for path in ("", "export.csv", "copy", "live"):
            r = self.login(self.t51).get(f"/s/{old.token}/c/{c.key}/{path}", follow=True)
            self.assertEqual(r.status_code, 404, path)
        self.assertEqual(self.login(self.t41).get(f"/s/{old.token}/c/{c.key}/", follow=True).status_code, 200)


class RowsQa(SchoolBase):
    def test_ten_class_comes_after_nine_and_grade_before_grade(self):
        late = self.seat("4-10", "사십", "g4", "10반")
        self.seat("4-9", "사구", "g4", "9반")
        units = [r["unit"] for r in audience_rows(self.hub, {"mode": "groups", "groups": ["g4", "g5"], "hr": True})]
        self.assertEqual(units, ["4-1", "4-2", "4-9", "4-10", "5-1", "5-2"])
        self.assertTrue(late.pk)

    def test_dept_names_with_position_and_spaces_pair_with_roster(self):
        Duty.objects.create(space=self.hub, order=0, dept="교무부", task="학적", who="부 장(교무), 사 일")
        rows = audience_rows(self.hub, {"mode": "dept", "depts": ["교무부"]})
        self.assertEqual([(r["who"], r["member"]) for r in rows], [("부장", self.boss), ("사일", self.t41)])

    def test_dept_cell_with_names_split_by_spaces_is_two_people(self):
        """담당 칸의 '오일 오이'(공백 구분)가 명부 두 사람의 줄이 되어야 한다."""
        Duty.objects.create(space=self.hub, order=0, dept="교무부", task="학적", who="오일 오이")
        rows = audience_rows(self.hub, {"mode": "dept", "depts": ["교무부"]})
        self.assertEqual([r["member"] for r in rows], [self.t51, self.t52])


class HwpHeadsQa(SchoolBase):
    def rows(self, *heads):
        return [{"r": i + 1, "head": h, "on": True} for i, h in enumerate(heads)]

    def test_long_heads_match_grade_too(self):
        roster = roster_rows(self.hub, "class")
        got = hwpform.assign(self.rows("5학년 1반", "4학년 1반", "4학년 2반", "5학년 2반"), roster, 0)
        self.assertEqual(dict(zip([r["unit"] for r in roster], got)), {"4-1": 2, "4-2": 3, "5-1": 1, "5-2": 4})

    def test_everyone_with_class_heads_never_lands_on_another_class(self):
        """전 교직원 + 학년반 머리표: 교무실 등 반 없는 사람도 다른 반 줄을 차지하지 않는다."""
        rows = audience_rows(self.hub, {"mode": "all"})
        got = hwpform.assign(self.rows("5-1", "4-1", "4-2"), rows, 0)
        pairs = {r["who"]: g for r, g in zip(rows, got) if g}
        self.assertEqual(pairs, {"오일": 1, "사일": 2, "사이": 3})

    def test_class_only_head_in_school_does_not_get_a_different_class(self):
        """학년 없는 '1반·2반' 머리표에 4-2가 '1반' 줄로 들어가면 안 된다(그 줄은 비우거나 추가 줄로)."""
        roster = [r for r in roster_rows(self.hub, "class") if r["unit"] == "4-2"]
        self.assertNotEqual(hwpform.assign(self.rows("1반", "2반"), roster, 0), [1])


class PollQa(SchoolBase):
    def test_edit_with_same_cell_count_returns_new_board(self):
        c = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        row = c.rows.first()
        Client().post(f"/c/{c.link_token}/u/{row.key}", {"f0": "3", "by": "사일"})
        owner = self.login(self.t41)
        board = owner.get(f"{self.base}/c/{c.key}/live")
        v = board.context["stamp"]
        self.assertEqual(owner.get(f"{self.base}/c/{c.key}/live", {"v": v}).status_code, 204)
        Client().post(f"/c/{c.link_token}/u/{row.key}", {"f0": "4", "by": "사일"})
        self.assertEqual(Cell.objects.get(row=row).value, "4")
        again = owner.get(f"{self.base}/c/{c.key}/live", {"v": v})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.context["units"][0]["lines"][0][0]["v"], "4")

    def test_outside_row_refreshes_board(self):
        r = self.login(self.t41).post(f"{self.base}/c/new", {"src": "sheet", "title": "밖", "col_name_0": "참가(명)", "aud": "groups", "aud_groups": ["g4"], "aud_hr": "on", "outside": "on"})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.latest("pk")
        owner = self.login(self.t41)
        v = owner.get(f"{self.base}/c/{c.key}/live").context["stamp"]
        Client().post(f"/c/{c.link_token}/outside", {"name": "방과후강사"})
        self.assertContains(owner.get(f"{self.base}/c/{c.key}/live", {"v": v}), "방과후강사")


class ReminderQa(SchoolBase):
    def test_group_row_reminder_reaches_group_members(self):
        """prototype: 그룹 칸은 내기 전까지 구성원 모두에게 온 것. 마감 🔔도 구성원에게 가야 한다."""
        c = self.make(who=self.boss, aud="pergroup", aud_pgroups=["g4"])
        Collection.objects.filter(pk=c.pk).update(due=timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1))
        bell.remind(timezone.localtime().replace(hour=9, minute=0))
        self.assertTrue(Notification.objects.filter(member=self.t41).exists())



class GroupBellTests(SchoolBase):
    """O1 후속: 그룹 칸 마감 🔔은 구성원마다 하나, 다시 불러도 그대로. 회원 없는 줄은 만든 사람의 합본에 표시만."""

    def due_soon(self, c):
        Collection.objects.filter(pk=c.pk).update(due=timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1))

    def at9(self):
        return timezone.localtime().replace(hour=9, minute=0)

    def test_each_group_member_once_others_none(self):
        c = self.make(who=self.boss, aud="pergroup", aud_pgroups=["g4"])
        self.due_soon(c)
        first = bell.remind(self.at9())
        members = Member.objects.in_space(self.hub).filter(group="g4")
        self.assertGreaterEqual(members.count(), 2)
        self.assertEqual(first, members.count())
        row = c.rows.get()
        for m in members:
            n = Notification.objects.get(member=m)
            self.assertEqual((n.row, n.link), (row, f"/c/{c.link_token}/u/{row.key}"))
        self.assertFalse(Notification.objects.filter(member=self.t51).exists())
        self.assertEqual(bell.remind(self.at9()), 0)

    def test_filled_group_row_quiet(self):
        c = self.make(who=self.boss, aud="pergroup", aud_pgroups=["g4"])
        self.due_soon(c)
        row, f = c.rows.get(), c.fields.first()
        for field in c.fields.all():
            Cell.objects.create(row=row, field=field, value="1", by="누구")
        self.assertIsNotNone(f)
        self.assertEqual(bell.remind(self.at9()), 0)

    def test_no_member_row_no_bell_and_shown_on_board(self):
        c = self.make(who=self.t41, aud="groups", aud_groups=["g4"], aud_hr="on")
        self.due_soon(c)
        c.rows.update(member=None)
        self.assertEqual(bell.remind(self.at9()), 0)
        Collection.objects.filter(pk=c.pk).update(audience={})
        r = self.login(self.t41).get(f"{self.base}/c/{c.key}/")
        self.assertContains(r, "마감 알림이 가지 않아요")
