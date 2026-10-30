"""C4 받는 사람: 동학년 명부로 줄 자동 생성(prototype TR.audRows) + 명부 밖 사람이 링크로 줄 고르기·이름 쓰기.

- 반별(prototype hrOnly '담임만 · 반별 1칸'): 반 번호가 있는 회원만(전담 빠짐), 반 순서. 직접 입력·학습준비물·받는 사람 칸 없는 표.
- 사람별(prototype '사람마다 한 줄', 직위·성명): 전담 포함 전원. 연수 이수.
- 명부 밖(allow_outside): 링크로 들어온 사람(다른 학년 교사 등)이 줄을 고르거나 이름으로 새 줄을 만든다. 화면은 C5.
"""
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from spaces.models import Member, School, Space

from .models import Cell, Collection, Row, get_by_link_or_404
from .recipients import OUTSIDE_MAX, OutsideClosed, add_outside_row, pick_row, roster_rows


class RecipientTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua = User.objects.create(username="google:a")
        # 들어온 순서와 반 순서를 일부러 다르게
        cls.m3 = Member.objects.create(space=cls.a, name="김가람", cls="4-3", user=cls.ua)
        cls.mj = Member.objects.create(space=cls.a, name="이전담", cls="4-전담")
        cls.m1 = Member.objects.create(space=cls.a, name="박나래", cls="4-1")
        cls.m10 = Member.objects.create(space=cls.a, name="최열반", cls="4-10")
        cls.mb = Member.objects.create(space=cls.b, name="옆학년", cls="5-1")

    def setUp(self):
        self.client.force_login(self.ua)

    def post(self, **data):
        r = self.client.post(f"/s/{self.a.token}/c/new", data)
        self.assertEqual(r.status_code, 302, r.content[:300])
        return Collection.objects.latest("pk")

    def rows(self, c):
        return [(r.pos, r.unit, r.who, r.member_id) for r in c.rows.all()]

    # ---- 명부로 줄 만들기
    def test_roster_rows_class_mode(self):
        self.assertEqual([(r["unit"], r["who"]) for r in roster_rows(self.a, "class")],
                         [("4-1", "박나래"), ("4-3", "김가람"), ("4-10", "최열반")])  # 전담 빠짐, 반 순서

    def test_roster_rows_people_mode(self):
        self.assertEqual([(r["unit"], r["who"]) for r in roster_rows(self.a, "people")],
                         [("4학년 1반", "박나래"), ("4학년 3반", "김가람"), ("4학년 10반", "최열반"), ("4학년 전담", "이전담")])

    def test_sheet_rows_from_roster(self):
        c = self.post(src="sheet", title="x", col_name_0="참가(명)")
        self.assertEqual(self.rows(c), [(0, "4-1", "박나래", self.m1.pk), (1, "4-3", "김가람", self.m3.pk),
                                        (2, "4-10", "최열반", self.m10.pk)])
        self.assertFalse(Cell.objects.exists())

    def test_preset_rows(self):
        sup = self.post(src="preset", preset="supplies")
        self.assertEqual([r.unit for r in sup.rows.all()], ["4-1", "4-3", "4-10"])
        tr = self.post(src="preset", preset="training")
        self.assertEqual([(r.unit, r.who) for r in tr.rows.all()][-1], ("4학년 전담", "이전담"))
        self.assertEqual(tr.rows.count(), 4)

    def test_paste_without_unit_columns_uses_roster(self):
        c = self.post(title="x", paste="날짜\t활동 내용\n\t\n")
        self.assertEqual(c.unit_head, ["학년반", "담임"])
        self.assertEqual([r.member_id for r in c.rows.all()], [self.m1.pk, self.m3.pk, self.m10.pk])

    def test_paste_with_unit_columns_uses_roster(self):
        # 리드 결정: 명부 우선(prototype TR.audRows·design). 표에서는 칸만.
        c = self.post(title="x", paste="반\t참가(명)\n4-1\t\n4-2\t")
        self.assertEqual([(r.unit, r.member_id) for r in c.rows.all()], [("4-1", self.m1.pk), ("4-3", self.m3.pk), ("4-10", self.m10.pk)])
        self.assertEqual([f.name for f in c.fields.all()], ["참가(명)"])

    def test_roster_never_crosses_space(self):
        c = self.post(src="preset", preset="training")
        self.assertNotIn(self.mb.pk, [r.member_id for r in c.rows.all()])

    def test_allow_outside_saved(self):
        self.assertTrue(self.post(src="sheet", title="x", col_name_0="a", outside="on").allow_outside)
        self.assertFalse(self.post(src="preset", preset="supplies").allow_outside)
        self.assertTrue(self.post(src="preset", preset="supplies", outside="on").allow_outside)

    # ---- 명부 밖: 링크로 줄 고르기·이름 쓰기(다른 학년 교사도)
    def test_outside_teacher_adds_row_by_link(self):
        c = self.post(src="sheet", title="x", col_name_0="a", outside="on")
        found = get_by_link_or_404(c.link_token)  # 받는 사람 링크(인증 없음)
        row = add_outside_row(found, "  옆학년‮ ")
        self.assertEqual((row.collection_id, row.unit, row.who, row.member_id, row.pos), (c.pk, "명부 밖", "옆학년", None, 3))
        self.assertEqual(add_outside_row(found, "옆학년").pk, row.pk)  # 같은 이름은 같은 줄
        self.assertEqual(c.rows.count(), 4)
        self.assertFalse(Cell.objects.exists())

    def test_outside_not_allowed_or_closed(self):
        c = self.post(src="sheet", title="x", col_name_0="a")
        with self.assertRaises(OutsideClosed):
            add_outside_row(c, "옆학년")
        c.allow_outside, c.closed_at = True, timezone.now()
        c.save()
        with self.assertRaises(OutsideClosed):
            add_outside_row(c, "옆학년")
        with self.assertRaises(ValueError):
            c.closed_at = None
            add_outside_row(c, " ​ ")

    def test_outside_row_cap(self):
        c = self.post(src="sheet", title="x", col_name_0="a", outside="on")
        Row.objects.bulk_create([Row(collection=c, pos=10 + i, unit="명부 밖", who=f"사람{i}") for i in range(OUTSIDE_MAX)])
        with self.assertRaises(OutsideClosed):
            add_outside_row(c, "한 명 더")

    def test_pick_row_only_in_same_collection(self):
        c1 = self.post(src="sheet", title="x", col_name_0="a")
        c2 = self.post(src="preset", preset="supplies")
        r1 = c1.rows.first()
        self.assertEqual(pick_row(c1, r1.key), r1)
        with self.assertRaises(Http404):
            pick_row(c2, r1.key)

    # ---- QA 추가(C4 경계)
    def test_qa_pick_row_other_space_or_bad_key_404(self):
        c = self.post(src="sheet", title="x", col_name_0="a")
        cb = Collection.objects.create(space=self.b, title="옆", source="sheet")
        rb = Row.objects.create(collection=cb, pos=0, unit="5-1", who="옆학년", member=self.mb)
        with self.assertRaises(Http404):
            pick_row(c, rb.key)  # 다른 공간 모으기의 줄 키
        with self.assertRaises(Http404):
            pick_row(cb, c.rows.first().key)  # 반대 방향
        for bad in ("", "nope", c.link_token, c.key):  # 토큰을 바꿔 끼워도
            with self.assertRaises(Http404):
                pick_row(c, bad)

    def test_qa_outside_same_name_reused_at_cap_but_not_after_close(self):
        c = self.post(src="sheet", title="x", col_name_0="a", outside="on")
        first = add_outside_row(c, "강사님")
        Row.objects.bulk_create([Row(collection=c, pos=100 + i, unit="명부 밖", who=f"사람{i}") for i in range(OUTSIDE_MAX - 1)])
        self.assertEqual(add_outside_row(c, "강사님").pk, first.pk)  # 상한에 닿아도 같은 이름은 그 줄
        with self.assertRaises(OutsideClosed):
            add_outside_row(c, "새 사람")
        c.closed_at = timezone.now()
        c.save()
        with self.assertRaises(OutsideClosed):
            add_outside_row(c, "강사님")  # 마감 뒤에는 같은 이름도 거부

    def test_qa_outside_rows_append_in_order_per_collection(self):
        c1 = self.post(src="sheet", title="x", col_name_0="a", outside="on")
        c2 = self.post(src="preset", preset="training", outside="on")
        r1, r2 = add_outside_row(c1, "가"), add_outside_row(c1, "나")
        r3 = add_outside_row(c2, "가")  # 다른 모으기의 같은 이름은 별개 줄
        self.assertEqual((r1.pos, r2.pos, r3.pos), (3, 4, 4))
        self.assertNotEqual(r1.pk, r3.pk)
        self.assertEqual(r3.collection_id, c2.pk)

    def test_qa_no_cells_for_any_source(self):
        self.post(src="preset", preset="supplies")
        self.post(src="preset", preset="training")
        self.post(title="x", paste="반\t참가(명)\n4-1\t3\n4-2\t5")  # 표에 값이 있어도 Cell은 만들지 않는다
        self.assertFalse(Cell.objects.exists())

    def test_qa_other_space_member_cannot_create(self):
        ub = get_user_model().objects.create(username="google:b")
        self.mb.user = ub
        self.mb.save()
        self.client.force_login(ub)
        r = self.client.post(f"/s/{self.a.token}/c/new", {"src": "preset", "preset": "training"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Collection.objects.filter(space=self.a).exists())
