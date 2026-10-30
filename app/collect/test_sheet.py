"""C3 새로 모으기 — 직접 입력(시트)·자주 쓰는 양식 2종. 결과는 prototype collectNew(sheet)·TR.preset과 같아야 한다.

받는 사람 줄은 C4(동학년 명부)에서 만든다. 지금은 줄 없이 칸만 만든다(C2와 같은 방식: 붙여넣은 표가 없으면 줄 없음).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Row
from .presets import PRESETS, TRAININGS


class SheetPresetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua = User.objects.create(username="google:a")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=User.objects.create(username="google:b"))

    def setUp(self):
        self.client.force_login(self.ua)

    def post(self, space=None, **data):
        return self.client.post(f"/s/{(space or self.a).token}/c/new", data)

    def sheet(self, space=None, **data):
        return self.post(space, src="sheet", **data)

    # ---- 화면
    def test_tabs(self):
        r = self.client.get(f"/s/{self.a.token}/c/new")
        for label in ("표 붙여넣기", "직접 입력", "자주 쓰는 양식", "한글 양식 올리기"):
            self.assertContains(r, label)
        self.assertContains(r, 'data-src="hwp" disabled')
        self.assertContains(r, "2학기 학습준비물 신청")
        self.assertContains(r, "법정의무연수")

    # ---- 직접 입력
    def test_sheet_create(self):
        r = self.sheet(title="방과후 수요조사", col_name_0="희망 인원", col_kind_0="", col_name_1="희망 프로그램",
                       col_kind_1="select", col_opts_1="로봇, 바둑 ,  ", col_name_2="  ", col_name_3="메모", col_kind_3="text",
                       multi="on")
        c = Collection.objects.get()
        self.assertRedirects(r, f"/s/{self.a.token}/c/{c.key}/", fetch_redirect_response=False)
        self.assertEqual((c.space, c.created_by, c.source, c.title, c.multi), (self.a, self.ma, "sheet", "방과후 수요조사", True))
        self.assertEqual(c.unit_head, ["학년반", "담임"])
        self.assertEqual([(f.pos, f.name, f.kind, f.options) for f in c.fields.all()],
                         [(0, "희망 인원", "number", []), (1, "희망 프로그램", "select", ["로봇", "바둑"]), (2, "메모", "text", [])])
        self.assertFalse(Row.objects.exists())  # 받는 사람 줄은 C4
        self.assertFalse(Cell.objects.exists())

    def test_sheet_defaults(self):
        # prototype: 제목이 비면 '첫 칸 조사', 고르기 보기가 비면 예·아니오
        self.sheet(col_name_0="참가 여부", col_kind_0="select")
        c = Collection.objects.get()
        self.assertEqual(c.title, "참가 여부 조사")
        self.assertEqual(c.fields.get().options, ["예", "아니오"])
        self.assertFalse(c.multi)

    def test_sheet_needs_a_column(self):
        r = self.sheet(title="x", col_name_0=" ")
        self.assertContains(r, "받을 칸 이름을 하나 이상", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_sheet_limits_and_bad_kind(self):
        data = {f"col_name_{i}": f"칸{i}" for i in range(10)}
        self.sheet(title="x", col_kind_0="calc", col_name_1="칸0", **{k: v for k, v in data.items() if k != "col_name_1"})
        c = Collection.objects.get()
        self.assertEqual([f.name for f in c.fields.all()], ["칸0", "칸0 (2)", "칸2", "칸3", "칸4", "칸5"])  # 6칸까지
        self.assertEqual(c.fields.first().kind, "text")

    # ---- 자주 쓰는 양식
    def test_preset_supplies(self):
        self.post(src="preset", preset="supplies")
        c = Collection.objects.get()
        self.assertEqual((c.source, c.title, c.multi, c.unit_head), ("preset", "2학기 학습준비물 신청", True, ["학년반", "담임"]))
        self.assertEqual([(f.name, f.kind) for f in c.fields.all()],
                         [("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc"), ("구입 링크", "url")])
        self.assertFalse(Cell.objects.exists())

    def test_preset_training(self):
        self.post(src="preset", preset="training", title="무시됨")
        c = Collection.objects.get()
        self.assertEqual((c.title, c.multi, c.unit_head), ("2026 교직원 법정의무연수 이수 현황", False, ["직위", "성명"]))
        self.assertEqual([f.name for f in c.fields.all()], TRAININGS)
        self.assertEqual(len(TRAININGS), 14)
        self.assertEqual({f.kind for f in c.fields.all()}, {"text"})

    def test_unknown_preset(self):
        self.assertEqual(self.post(src="preset", preset="nope").status_code, 400)
        self.assertEqual(self.post(src="nope", title="x").status_code, 400)
        self.assertFalse(Collection.objects.exists())

    # ---- 격리
    def test_other_space_404(self):
        self.assertEqual(self.sheet(self.b, title="x", col_name_0="a").status_code, 404)
        self.assertEqual(self.post(self.b, src="preset", preset="supplies").status_code, 404)
        self.assertFalse(Collection.objects.exists())

    def test_fields_stay_in_own_collection(self):
        self.post(src="preset", preset="supplies")
        self.sheet(title="둘째", col_name_0="메모")
        c1, c2 = Collection.objects.order_by("pk")
        self.assertEqual(list(c2.fields.values_list("name", "pos")), [("메모", 0)])
        self.assertEqual(c1.fields.count(), len(PRESETS["supplies"]["fields"]))
        self.assertFalse(Field.objects.filter(collection=c2, name="물품명").exists())
        self.assertEqual(set(Field.objects.in_space(self.a).values_list("collection", flat=True)), {c1.pk, c2.pk})
