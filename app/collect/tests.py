"""C1 모으기 모델 불변식: 공간 격리(Collection·Row·Cell), 링크 토큰, 삭제 범위, 칸 하나에 값 하나."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.test import TestCase, override_settings
from django.shortcuts import get_object_or_404
from django.urls import path

from spaces.models import Member, School, Space, get_in_space_or_404

from .models import Cell, Collection, Field, Row, get_by_link_or_404


def collection_detail(request, key):
    return HttpResponse(get_in_space_or_404(Collection, request, key=key).title)


def row_detail(request, key):
    return HttpResponse(get_in_space_or_404(Row, request, key=key).unit)


def cell_detail(request, pk):
    # 화면에서는 칸을 주소로 쓰지 않는다. 경로(row→collection→space) 격리만 확인한다.
    return HttpResponse(get_in_space_or_404(Cell, request, pk=pk).value)


urlpatterns = [
    path("s/<str:space_token>/c/<str:key>/", collection_detail),
    path("s/<str:space_token>/r/<str:key>/", row_detail),
    path("s/<str:space_token>/cell/<int:pk>/", cell_detail),
]


def make_collection(space, title):
    c = Collection.objects.create(space=space, title=title, source=Collection.Source.SHEET)
    f = Field.objects.create(collection=c, pos=0, name="희망 인원", kind=Field.Kind.NUMBER)
    r = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람")
    cell = Cell.objects.create(row=r, field=f, value="3", by="김가람")
    return c, f, r, cell


@override_settings(ROOT_URLCONF=__name__)
class CollectIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        cls.ua = get_user_model().objects.create(username="google:a")
        Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.ca, _, cls.ra, cls.cella = make_collection(cls.a, "우리 학년")
        cls.cb, _, cls.rb, cls.cellb = make_collection(cls.b, "옆 학년")

    def setUp(self):
        self.client.force_login(self.ua)

    def get(self, kind, key):
        return self.client.get(f"/s/{self.a.token}/{kind}/{key}/")

    def test_same_space_200(self):
        self.assertEqual(self.get("c", self.ca.key).content.decode(), "우리 학년")
        self.assertEqual(self.get("r", self.ra.key).content.decode(), "4-1")
        self.assertEqual(self.get("cell", self.cella.pk).status_code, 200)

    def test_other_space_collection_404(self):
        self.assertEqual(self.get("c", self.cb.key).status_code, 404)

    def test_other_space_row_404(self):
        self.assertEqual(self.get("r", self.rb.key).status_code, 404)

    def test_other_space_cell_404(self):
        self.assertEqual(self.get("cell", self.cellb.pk).status_code, 404)

    def test_in_space_lists_only_own(self):
        self.assertEqual(list(Collection.objects.in_space(self.a)), [self.ca])
        self.assertEqual(list(Row.objects.in_space(self.a)), [self.ra])
        self.assertEqual(list(Cell.objects.in_space(self.a)), [self.cella])
        self.assertEqual(list(Field.objects.in_space(self.a)), list(self.ca.fields.all()))

    def test_in_space_without_space_raises(self):
        for model in (Collection, Field, Row, Cell):
            with self.assertRaises(ValueError):
                model.objects.in_space(None)


class LinkTokenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.space = Space.objects.create(school=school, grade=4, name="4학년")

    def test_tokens_long_unique_and_distinct(self):
        c1, *_ = make_collection(self.space, "하나")
        c2, *_ = make_collection(self.space, "둘")
        for c in (c1, c2):
            # token_urlsafe(24) = 32글자(192비트). 순번 ID가 섞이지 않는다.
            self.assertGreaterEqual(len(c.link_token), 32)
            self.assertGreaterEqual(len(c.key), 32)
            self.assertNotEqual(c.link_token, c.key)
            self.assertNotIn(str(c.pk), (c.link_token, c.key))
        self.assertNotEqual(c1.link_token, c2.link_token)

    def test_link_token_unique_constraint(self):
        c1, *_ = make_collection(self.space, "하나")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Collection.objects.create(space=self.space, title="겹침", source="sheet", link_token=c1.link_token)

    def test_get_by_link(self):
        c, *_ = make_collection(self.space, "하나")
        self.assertEqual(get_by_link_or_404(c.link_token), c)
        for bad in ("", "x" * 32, c.key, str(c.pk)):
            with self.assertRaises(Http404):
                get_by_link_or_404(bad)


class ModelRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.space = Space.objects.create(school=school, grade=4, name="4학년")

    def test_one_cell_per_row_field(self):
        c, f, r, _ = make_collection(self.space, "하나")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cell.objects.create(row=r, field=f, value="4")

    def test_delete_collection_cascades_to_fields_rows_cells_only(self):
        c, *_ = make_collection(self.space, "하나")
        keep, *_ = make_collection(self.space, "남김")
        c.delete()
        self.assertEqual(Field.objects.filter(collection=c.pk).count(), 0)
        self.assertEqual(Row.objects.filter(collection=c.pk).count(), 0)
        self.assertEqual(Cell.objects.filter(row__collection=c.pk).count(), 0)
        self.assertEqual(Cell.objects.filter(row__collection=keep).count(), 1)

    def test_delete_field_or_row_removes_its_cells(self):
        c, f, r, _ = make_collection(self.space, "하나")
        f2 = Field.objects.create(collection=c, pos=1, name="비고")
        Cell.objects.create(row=r, field=f2, value="없음")
        f.delete()
        self.assertEqual(list(Cell.objects.filter(row=r).values_list("value", flat=True)), ["없음"])
        r.delete()
        self.assertFalse(Cell.objects.filter(field=f2).exists())

    def test_delete_space_cascades_collections(self):
        make_collection(self.space, "하나")
        self.space.delete()
        self.assertFalse(Collection.objects.exists())
        self.assertFalse(Cell.objects.exists())

    def test_deleting_member_keeps_collection_and_rows(self):
        m = Member.objects.create(space=self.space, name="김가람", cls="4-1")
        c = Collection.objects.create(space=self.space, title="하나", source="sheet", created_by=m)
        r = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=m)
        m.delete()
        c.refresh_from_db()
        r.refresh_from_db()
        self.assertIsNone(c.created_by)
        self.assertIsNone(r.member)
        self.assertEqual(r.who, "김가람")


class QaPrototypeShapeTests(TestCase):
    """QA(C1): prototype collectNew 결과 객체를 손으로 옮겨 이 모델로 되살릴 수 있는지."""

    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.space = Space.objects.create(school=school, grade=4, name="4학년")

    def test_collect_new_sheet_groups_hronly(self):
        # app.js collectNew: {title, aud:{mode:'groups',hrOnly}, ext, fields:[{n,t,o}], rows:[{unit,who,v,done,at}]}
        proto = {
            "title": "2학기 방과후 수요조사", "ext": True,
            "fields": [{"n": "희망 인원", "t": "number"}, {"n": "1순위 프로그램", "t": "select", "o": ["로봇코딩", "바둑"]},
                       {"n": "비고", "t": "text"}],
            "rows": [{"unit": "4-1", "who": "김가람", "v": ["12", "바둑", ""]}, {"unit": "4-2", "who": "이나래", "v": ["", "", ""]}],
        }
        c = Collection.objects.create(space=self.space, title=proto["title"], source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"], allow_outside=proto["ext"])
        fs = [Field.objects.create(collection=c, pos=i, name=f["n"], kind=f["t"], options=f.get("o", []))
              for i, f in enumerate(proto["fields"])]
        for i, r in enumerate(proto["rows"]):
            row = Row.objects.create(collection=c, pos=i, unit=r["unit"], who=r["who"])
            for f, v in zip(fs, r["v"]):
                if v:
                    Cell.objects.create(row=row, field=f, value=v, by=r["who"])
        for f in fs:
            f.full_clean()  # kind 값이 choices 안에 있어야 한다
        back = [[r.unit, r.who] + [next((x.value for x in r.cells.all() if x.field_id == f.pk), "") for f in c.fields.all()]
                for r in c.rows.all()]
        self.assertEqual(back, [["4-1", "김가람", "12", "바둑", ""], ["4-2", "이나래", "", "", ""]])
        done = [r.cells.exists() for r in c.rows.all()]
        self.assertEqual(done, [True, False])

    def test_collect_new_items_multi_and_pergroup(self):
        # 학습준비물(kind:'items'): 같은 반 줄 여러 개 + calc/url 칸. 그룹별(pergroup): who 없이 by만.
        c = Collection.objects.create(space=self.space, title="학습준비물", source=Collection.Source.PRESET, multi=True)
        for i, (n, t) in enumerate([("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc"), ("구입 링크", "url")]):
            Field.objects.create(collection=c, pos=i, name=n, kind=t).full_clean()
        Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람")
        Row.objects.create(collection=c, pos=1, unit="4-1", who="김가람")
        self.assertEqual(c.rows.filter(unit="4-1").count(), 2)
        g = Collection.objects.create(space=self.space, title="체험학습 평가", source=Collection.Source.SHEET,
                                      unit_head=["그룹", "쓴 사람"])
        f = Field.objects.create(collection=g, pos=0, name="잘된 점")
        r = Row.objects.create(collection=g, pos=0, unit="4학년")
        Cell.objects.create(row=r, field=f, value="과학 체험 부스", by="황보람")
        self.assertEqual((r.who, r.cells.get().by), ("", "황보람"))


@override_settings(ROOT_URLCONF=__name__)
class QaIsolationEdgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        s1 = School.objects.create(neis_atpt="S10", neis_code="1111111", name="가초")
        s2 = School.objects.create(neis_atpt="B10", neis_code="2222222", name="나초")
        cls.a = Space.objects.create(school=s1, grade=4, name="4학년")
        cls.other_school = Space.objects.create(school=s2, grade=4, name="4학년")
        cls.ua = get_user_model().objects.create(username="google:qa")
        Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.ca, cls.fa, cls.ra, _ = make_collection(cls.a, "우리")
        cls.cx, cls.fx, cls.rx, cls.cellx = make_collection(cls.other_school, "다른 학교")

    def test_other_school_same_grade_404(self):
        self.client.force_login(self.ua)
        for kind, key in (("c", self.cx.key), ("r", self.rx.key), ("cell", self.cellx.pk)):
            self.assertEqual(self.client.get(f"/s/{self.a.token}/{kind}/{key}/").status_code, 404)

    def test_link_token_in_space_key_slot_404(self):
        # 받는 사람 링크 토큰을 공간 안 주소(key 자리)에 끼워도 안 열린다.
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/c/{self.ca.link_token}/").status_code, 404)

    def test_field_other_space_404(self):
        with self.assertRaises(Http404):
            get_object_or_404(Field.objects.in_space(self.a), pk=self.fx.pk)

    def test_row_key_token(self):
        self.assertGreaterEqual(len(self.ra.key), 32)
        self.assertNotEqual(self.ra.key, self.rx.key)
        self.assertNotIn(self.ra.key, (self.ca.key, self.ca.link_token))
