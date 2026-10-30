"""C5 받는 사람 제출: 링크 하나 → 줄 고르기 → 제출·수정. 로그인 없음, 대리 제출 허용, 내는 사람·시각 기록.

불변식
- 인증 없이 열고 낸다. 인증 없는 폼도 CSRF 토큰을 요구한다.
- Cell은 낼 때만 생긴다(빈 칸 사전 생성 없음). Cell의 칸·줄은 같은 모으기 소속이어야 한다(서버 검사).
- 다른 모으기의 줄 키·잘못된 링크 토큰은 404. 마감(closed_at)되면 낼 수 없다.
- 받는 사람은 다른 줄의 값을 보지 못한다(냈는지만). 이 브라우저가 낸 줄만 고칠 때 값을 미리 채운다.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Row
from .recipients import OUTSIDE_UNIT
from .submit import save_cell


def make(space, title="학예회 참가 인원", multi=False, **kw):
    c = Collection.objects.create(space=space, title=title, source=Collection.Source.SHEET,
                                  unit_head=["학년반", "담임"], multi=multi, **kw)
    return c


class SubmitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1")
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2")
        cls.c = make(cls.a, created_by=cls.owner)
        cls.f_yes = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.f_pick = Field.objects.create(collection=cls.c, pos=1, name="버스", kind="select", options=["예", "아니오"])
        cls.f_memo = Field.objects.create(collection=cls.c, pos=2, name="비고", kind="text")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        # 다른 공간의 모으기
        cls.other = make(cls.b, title="옆 학년")
        cls.of = Field.objects.create(collection=cls.other, pos=0, name="참가(명)", kind="number")
        cls.orow = Row.objects.create(collection=cls.other, pos=0, unit="5-1", who="옆학년")

    def url(self, row=None, c=None):
        c = c or self.c
        return f"/c/{c.link_token}" + (f"/u/{row.key}" if row else "")

    def send(self, row, client=None, **data):
        data = {"f0": "21", "f1": "예", "f2": "", "by": "", **data}
        return (client or self.client).post(self.url(row), data)

    # ---- 로그인 없이 고르기 → 내기 → 고치기
    def test_pick_without_login(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "어느 반 칸을 채우시나요?")
        self.assertContains(r, self.url(self.r1))
        self.assertContains(r, "0</b> / 2반")
        self.assertNotContains(r, "로그아웃")

    def test_submit_then_edit(self):
        r = self.client.get(self.url(self.r2))
        self.assertContains(r, "제출하기")
        self.assertContains(r, 'name="csrfmiddlewaretoken"')
        r = self.send(self.r2, by="박서준")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "제출했어요")
        self.assertEqual({c.field_id: c.value for c in self.r2.cells.all()},
                         {self.f_yes.pk: "21", self.f_pick.pk: "예", self.f_memo.pk: ""})
        first = self.r2.cells.get(field=self.f_yes)
        self.assertEqual(first.by, "박서준")
        self.assertIsNotNone(first.at)
        # 같은 브라우저: 고치기 화면에 낸 값이 채워져 있고, 다시 내면 바뀐다(줄 하나에 값 하나)
        r = self.client.get(self.url(self.r2))
        self.assertContains(r, "고쳐서 다시 내기")
        self.assertContains(r, 'value="21"')
        self.send(self.r2, f0="19", by="박서준")
        self.assertEqual(self.r2.cells.get(field=self.f_yes).value, "19")
        self.assertEqual(Cell.objects.filter(row=self.r2).count(), 3)
        r = self.client.get(self.url())
        self.assertContains(r, "1</b> / 2반")

    def test_other_browser_sees_done_but_not_values(self):
        self.send(self.r2, f0="21", by="박서준")
        r = Client().get(self.url(self.r2))
        self.assertContains(r, "이미 냈어요")
        self.assertNotContains(r, 'value="21"')
        r = Client().get(self.url())
        self.assertNotContains(r, ">21<")

    def test_overwritten_values_not_prefilled_for_earlier_sender(self):
        # 보안 검토: A가 먼저 내 두고 B가 덮어쓰면, A 세션의 고치기 화면에 B 값이 보이면 안 된다
        a, b = Client(), Client()
        self.send(self.r2, client=a, f0="11", by="갑")
        self.send(self.r2, client=b, f0="37", f2="B만 아는 메모", by="을")
        r = a.get(self.url(self.r2))
        self.assertNotContains(r, 'value="37"')
        self.assertNotContains(r, "B만 아는 메모")
        self.assertContains(r, "다른 분이 고쳤어요")
        self.assertContains(b.get(self.url(self.r2)), 'value="37"')  # 마지막으로 낸 세션은 그대로 고친다
        self.send(self.r2, client=a, f0="12", by="갑")  # A가 다시 내면 A가 이어서 고친다
        self.assertContains(a.get(self.url(self.r2)), 'value="12"')
        self.assertNotContains(b.get(self.url(self.r2)), 'value="12"')

    def test_proxy_submit_records_by(self):
        # 대리 제출: 박서준이 김가람 줄을 대신 낸다
        r = self.send(self.r1, by="박서준")
        self.assertContains(r, "대신 냄")
        self.assertEqual(set(self.r1.cells.values_list("by", flat=True)), {"박서준"})

    def test_remembers_name_in_session(self):
        self.send(self.r1, by="박서준")
        r = self.client.get(self.url())
        self.assertContains(r, "이 PC에서 쓰던 이름이에요")
        r = self.client.get(self.url(self.r2))
        self.assertContains(r, 'value="박서준"')  # 내는 사람 미리 채움

    def test_logged_in_member_prefilled(self):
        User = get_user_model()
        u = User.objects.create(username="google:x")
        self.m2.user = u
        self.m2.save()
        self.client.force_login(u)
        r = self.client.get(self.url())
        self.assertContains(r, "이 PC에서 쓰던 이름이에요")
        r = self.client.get(self.url(self.r1))
        self.assertContains(r, 'name="by" autocomplete="name" value="박서준"')

    # ---- 검사
    def test_required_and_kinds(self):
        r = self.send(self.r1, f0="", f1="몰라")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "숫자로 써 주세요", status_code=400)
        self.assertContains(r, "보기에서 골라 주세요", status_code=400)
        r = self.send(self.r1, f0="열")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Cell.objects.exists())

    def test_number_with_comma(self):
        self.send(self.r1, f0="1,200")
        self.assertEqual(self.r1.cells.get(field=self.f_yes).value, "1200")

    def test_no_precreated_cells(self):
        self.client.get(self.url())
        self.client.get(self.url(self.r1))
        self.assertFalse(Cell.objects.exists())

    # ---- 섞기·토큰·마감
    def test_bad_token_404(self):
        r = self.client.get("/c/nope")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "링크를 찾을 수 없어요", status_code=404)
        self.assertEqual(self.client.post("/c/nope/u/x", {"f0": "1"}).status_code, 404)

    def test_other_collection_row_404(self):
        r = self.client.post(f"/c/{self.c.link_token}/u/{self.orow.key}", {"f0": "1"})
        self.assertEqual(r.status_code, 404)
        r = self.client.get(f"/c/{self.c.link_token}/u/{self.orow.key}")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Cell.objects.exists())

    def test_save_cell_rejects_mixed_collection(self):
        with self.assertRaises(ValueError):
            save_cell(self.r1, self.of, "1", "x")
        with self.assertRaises(ValueError):
            save_cell(self.orow, self.f_yes, "1", "x")
        self.assertFalse(Cell.objects.exists())

    def test_closed_rejects(self):
        Collection.objects.filter(pk=self.c.pk).update(closed_at=timezone.now())
        r = self.send(self.r1)
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "마감됐어요", status_code=403)
        self.assertFalse(Cell.objects.exists())
        self.assertContains(self.client.get(self.url()), "마감됐어요")

    def test_late_shown(self):
        Collection.objects.filter(pk=self.c.pk).update(due=timezone.now() - timedelta(hours=1))
        self.send(self.r1)
        self.assertContains(self.client.get(self.url()), "늦게 냈어요")

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.post(self.url(self.r1), {"f0": "1", "f1": "예"}).status_code, 403)
        c.get(self.url(self.r1))
        token = c.cookies["csrftoken"].value
        r = c.post(self.url(self.r1), {"f0": "1", "f1": "예", "csrfmiddlewaretoken": token})
        self.assertEqual(r.status_code, 200)

    def test_htmx_returns_main_only(self):
        r = self.client.post(self.url(self.r1), {"f0": "3", "f1": "예"}, headers={"HX-Request": "true"})
        self.assertContains(r, "제출했어요")
        self.assertNotContains(r, "<html")

    # ---- 명부 밖
    def test_outside_name_then_submit(self):
        Collection.objects.filter(pk=self.c.pk).update(allow_outside=True)
        self.assertContains(self.client.get(self.url()), "명부에 없으면")
        r = self.client.post(self.url() + "/outside", {"name": "최보건"})
        self.assertEqual(r.status_code, 303)
        row = Row.objects.get(collection=self.c, unit=OUTSIDE_UNIT, who="최보건")
        self.assertEqual(r["Location"], self.url(row))
        self.assertContains(self.client.get(r["Location"]), 'value="최보건"')  # 쓴 이름을 내는 사람으로 기억
        self.send(row, by="최보건")
        self.assertEqual(row.cells.get(field=self.f_yes).by, "최보건")
        self.assertContains(self.client.get(self.url()), "1</b> / 3반")

    def test_outside_not_allowed(self):
        r = self.client.post(self.url() + "/outside", {"name": "최보건"})
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(self.client.get(self.url()), "명부에 없으면")
        self.assertFalse(Row.objects.filter(unit=OUTSIDE_UNIT).exists())


class MultiSubmitTests(TestCase):
    """multi(학습준비물): 한 받는 사람이 줄을 여러 개 낸다. 같은 받는 곳 줄을 더 만들고, 지운 줄은 없앤다."""

    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.c = make(cls.a, title="학습준비물", multi=True)
        names = [("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc"), ("구입 링크", "url")]
        cls.fs = [Field.objects.create(collection=cls.c, pos=i, name=n, kind=k) for i, (n, k) in enumerate(names)]
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람")
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준")
        other = make(cls.a, title="다른 모으기", multi=True)
        cls.orow = Row.objects.create(collection=other, pos=0, unit="4-1", who="김가람")

    def url(self):
        return f"/c/{self.c.link_token}/u/{self.r1.key}"

    def lines(self, *items, keys=None):
        keys = keys or [""] * len(items)
        return {"line": keys, "f0": [i[0] for i in items], "f1": [i[1] for i in items],
                "f2": [i[2] for i in items], "f4": [i[3] for i in items], "by": "김가람"}

    def test_add_lines_and_calc_not_stored(self):
        keys = [self.r1.key, ""]
        r = self.client.post(self.url(), self.lines(("색종이", "2", "1500", ""), ("풀", "3", "800", "https://x.kr/a"), keys=keys))
        self.assertEqual(r.status_code, 200, r.content[:400])
        rows = list(self.c.rows.filter(unit="4-1"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].pk, self.r1.pk)
        self.assertEqual(rows[1].pos, 2)  # 마지막 줄 다음
        self.assertEqual(rows[1].who, "김가람")
        self.assertFalse(Cell.objects.filter(field=self.fs[3]).exists())  # 자동 계산 칸은 저장 안 함
        self.assertEqual(rows[1].cells.get(field=self.fs[4]).value, "https://x.kr/a")
        r = self.client.get(f"/c/{self.c.link_token}")
        self.assertContains(r, "1</b> / 2반")  # 받는 곳 단위로 센다

    def test_remove_line(self):
        self.client.post(self.url(), self.lines(("색종이", "2", "1500", ""), ("풀", "3", "800", ""), keys=[self.r1.key, ""]))
        extra = self.c.rows.get(unit="4-1", pos=2)
        self.client.post(self.url(), self.lines(("풀", "3", "800", ""), keys=[extra.key]))
        self.assertFalse(self.r1.cells.exists())  # 처음 줄(명부 줄)은 남기고 값만 지움
        self.assertEqual(self.c.rows.filter(unit="4-1").count(), 2)
        self.assertEqual(extra.cells.get(field=self.fs[0]).value, "풀")

    def test_foreign_line_key_rejected(self):
        for key in (self.orow.key, self.r2.key):
            r = self.client.post(self.url(), self.lines(("색종이", "2", "1500", ""), keys=[key]))
            self.assertEqual(r.status_code, 400)
        self.assertFalse(Cell.objects.exists())

    def test_bad_url_and_empty(self):
        r = self.client.post(self.url(), self.lines(("색종이", "2", "1500", "javascript:alert(1)")))
        self.assertEqual(r.status_code, 400)
        r = self.client.post(self.url(), self.lines(("", "", "", "")))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Cell.objects.exists())


class PrototypeRuleTests(TestCase):
    """리드 결정: 필수 칸은 prototype 그대로 — 모든 칸 필수, 이름이 '비고'인 칸만 예외(collect).
    여러 줄(itemsForm)은 구입 링크만 선택. 연수 이수(matrixForm)는 다 못 채워도 저장, '해당 없음' 체크, 다 채워야 제출함."""

    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.c = make(cls.a, title="체험학습")
        cls.f_act = Field.objects.create(collection=cls.c, pos=0, name="활동 내용", kind="text")
        cls.f_link = Field.objects.create(collection=cls.c, pos=1, name="자료 링크", kind="url")
        cls.f_memo = Field.objects.create(collection=cls.c, pos=2, name="비고", kind="text")
        cls.r = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람")
        cls.t = Collection.objects.create(space=cls.a, title="연수", source=Collection.Source.PRESET,
                                          unit_head=["직위", "성명"])
        cls.tf = [Field.objects.create(collection=cls.t, pos=i, name=n, kind="text") for i, n in enumerate(["청렴교육", "안전교육", "통일교육"])]
        cls.tr = Row.objects.create(collection=cls.t, pos=0, unit="4학년 1반", who="김가람")
        cls.tr2 = Row.objects.create(collection=cls.t, pos=1, unit="4학년 2반", who="박서준")
        cls.m = make(cls.a, title="준비물", multi=True)
        for i, (n, k) in enumerate([("물품명", "text"), ("수량", "number"), ("단가", "number"), ("구입 링크", "url")]):
            Field.objects.create(collection=cls.m, pos=i, name=n, kind=k)
        cls.mr = Row.objects.create(collection=cls.m, pos=0, unit="4-1", who="김가람")

    def u(self, c, row):
        return f"/c/{c.link_token}/u/{row.key}"

    def test_text_and_url_required_except_memo(self):
        r = self.client.post(self.u(self.c, self.r), {"f0": "", "f1": "", "f2": ""})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "써 주세요", count=2, status_code=400)
        self.assertFalse(Cell.objects.exists())
        r = self.client.post(self.u(self.c, self.r), {"f0": "숲 체험", "f1": "https://x.kr", "f2": ""})
        self.assertEqual(r.status_code, 200)

    def test_multi_link_optional_name_required(self):
        u = self.u(self.m, self.mr)
        r = self.client.post(u, {"line": [""], "f0": [""], "f1": ["2"], "f2": ["100"], "f3": [""]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(u, {"line": [""], "f0": ["풀"], "f1": ["2"], "f2": ["100"], "f3": [""]})
        self.assertEqual(r.status_code, 200)

    def test_matrix_form_has_not_applicable(self):
        r = self.client.get(self.u(self.t, self.tr))
        self.assertContains(r, "해당 없음")
        self.assertContains(r, "이수번호 붙여넣기")
        self.assertContains(r, "저장하기")

    def test_matrix_partial_save_and_done(self):
        u = self.u(self.t, self.tr)
        r = self.client.post(u, {"f0": "A-123", "f1": "", "f2": ""})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "저장했어요")
        self.assertEqual({c.field_id: c.value for c in self.tr.cells.all()}, {self.tf[0].pk: "A-123"})  # 채운 칸만
        self.assertContains(self.client.get(f"/c/{self.t.link_token}"), "0</b> / 2명")  # 다 채워야 제출함
        r = self.client.post(u, {"f0": "A-123", "f1": "B-9", "na2": "on", "f2": "무시"})
        self.assertContains(r, "제출했어요")
        self.assertEqual(self.tr.cells.get(field=self.tf[2]).value, "해당 없음")
        self.assertContains(self.client.get(f"/c/{self.t.link_token}"), "1</b> / 2명")
        self.client.post(u, {"f0": "", "f1": "B-9", "na2": "on"})  # 지운 칸은 값도 지움
        self.assertFalse(self.tr.cells.filter(field=self.tf[0]).exists())

    def test_matrix_empty_save_creates_nothing(self):
        r = self.client.post(self.u(self.t, self.tr2), {"f0": "", "f1": "", "f2": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Cell.objects.exists())


class QAEdgeTests(TestCase):
    """QA(C5) 경계 사례: 다른 토큰 끼우기, 다른 학년 회원, 연수 판별 오탐, 마감 뒤 명부 밖·여러 줄, 이스케이프."""

    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        cls.mb = Member.objects.create(space=cls.b, name="옆학년쌤", cls="5-1")
        cls.c = make(cls.a)
        cls.f0 = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.r = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람")
        # 연수 양식과 머리글은 같지만 직접 만든 표(SHEET) — 연수 규칙(부분 저장)이 걸리면 안 된다
        cls.s = Collection.objects.create(space=cls.a, title="직위표", source=Collection.Source.SHEET, unit_head=["직위", "성명"])
        cls.sf = [Field.objects.create(collection=cls.s, pos=i, name=n, kind="text") for i, n in enumerate(["청렴교육", "안전교육"])]
        cls.sr = Row.objects.create(collection=cls.s, pos=0, unit="4학년 1반", who="김가람")
        # 학습준비물 양식(PRESET, 학년반) — 연수 규칙이 걸리면 안 된다
        cls.p = Collection.objects.create(space=cls.a, title="준비물", source=Collection.Source.PRESET,
                                          unit_head=["학년반", "담임"], multi=True)
        for i, (n, k) in enumerate([("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc"), ("구입 링크", "url")]):
            Field.objects.create(collection=cls.p, pos=i, name=n, kind=k)
        cls.pr = Row.objects.create(collection=cls.p, pos=0, unit="4-1", who="김가람")

    def test_swapped_tokens_404(self):
        for tok in (self.c.key, self.a.token, self.r.key):
            self.assertEqual(self.client.get(f"/c/{tok}").status_code, 404, tok)
        self.assertEqual(self.client.post(f"/c/{self.c.key}/u/{self.r.key}", {"f0": "1"}).status_code, 404)
        # 줄 키 자리에 모으기 링크를 넣어도 404
        self.assertEqual(self.client.get(f"/c/{self.c.link_token}/u/{self.c.link_token}").status_code, 404)
        self.assertFalse(Cell.objects.exists())

    def test_other_grade_member_not_treated_as_me(self):
        u = get_user_model().objects.create(username="google:b")
        self.mb.user = u
        self.mb.save()
        self.client.force_login(u)
        r = self.client.get(f"/c/{self.c.link_token}/u/{self.r.key}")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "옆학년쌤")
        self.assertNotContains(self.client.get(f"/c/{self.c.link_token}"), "이 PC에서 쓰던 이름이에요")

    def test_sheet_with_position_head_is_not_matrix(self):
        u = f"/c/{self.s.link_token}/u/{self.sr.key}"
        r = self.client.get(u)
        self.assertContains(r, "제출하기")
        self.assertNotContains(r, "이수번호 붙여넣기")
        r = self.client.post(u, {"f0": "A-1", "f1": "", "na1": "on"})
        self.assertEqual(r.status_code, 400)  # 모든 칸 필수, '해당 없음' 무시
        self.assertFalse(Cell.objects.exists())

    def test_supplies_preset_is_not_matrix(self):
        u = f"/c/{self.p.link_token}/u/{self.pr.key}"
        self.assertNotContains(self.client.get(u), "이수번호 붙여넣기")
        r = self.client.post(u, {"line": [""], "f0": [""], "f1": ["2"], "f2": ["100"], "f4": [""]})
        self.assertEqual(r.status_code, 400)  # 물품명 필수(연수처럼 부분 저장되면 안 됨)

    def test_multi_calc_post_ignored_and_line_cap(self):
        u = f"/c/{self.p.link_token}/u/{self.pr.key}"
        r = self.client.post(u, {"line": [""], "f0": ["풀"], "f1": ["2"], "f2": ["100"], "f3": ["999999"], "f4": [""]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Cell.objects.filter(field__kind="calc").exists())
        before = self.p.rows.count()
        n = 51
        r = self.client.post(u, {"line": [""] * n, "f0": ["풀"] * n, "f1": ["1"] * n, "f2": ["1"] * n, "f4": [""] * n})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.p.rows.count(), before)

    def test_multi_values_hidden_from_other_browser(self):
        u = f"/c/{self.p.link_token}/u/{self.pr.key}"
        self.client.post(u, {"line": [self.pr.key, ""], "f0": ["색종이", "크레파스"], "f1": ["2", "1"], "f2": ["1500", "3000"], "f4": ["", ""]})
        r = Client().get(u)
        self.assertNotContains(r, "크레파스")
        self.assertNotContains(r, "색종이")
        extra = self.p.rows.exclude(pk=self.pr.pk).get()
        r = self.client.get(f"/c/{self.p.link_token}/u/{extra.key}")  # 덧붙인 줄 키로 와도 같은 묶음
        self.assertContains(r, f'action="/c/{self.p.link_token}/u/{self.pr.key}"')
        self.assertContains(r, "크레파스")

    def test_closed_blocks_outside_and_multi(self):
        Collection.objects.filter(pk__in=[self.c.pk, self.p.pk]).update(closed_at=timezone.now(), allow_outside=True)
        r = self.client.post(f"/c/{self.c.link_token}/outside", {"name": "최보건"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Row.objects.filter(unit=OUTSIDE_UNIT).exists())
        r = self.client.post(f"/c/{self.p.link_token}/u/{self.pr.key}",
                             {"line": ["", ""], "f0": ["풀", "가위"], "f1": ["1", "1"], "f2": ["1", "1"], "f4": ["", ""]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.p.rows.count(), 1)
        self.assertFalse(Cell.objects.exists())

    def test_outside_name_escaped_and_same_name_reused(self):
        Collection.objects.filter(pk=self.c.pk).update(allow_outside=True)
        r1 = self.client.post(f"/c/{self.c.link_token}/outside", {"name": "<b>최</b>"})
        r2 = Client().post(f"/c/{self.c.link_token}/outside", {"name": "<b>최</b>"})
        self.assertEqual(r1["Location"], r2["Location"])
        r = self.client.get(f"/c/{self.c.link_token}")
        self.assertNotContains(r, "<b>최</b>")
        self.assertContains(r, "&lt;b&gt;최&lt;/b&gt;")

    def test_proxy_then_owner_edit_updates_by(self):
        u = f"/c/{self.c.link_token}/u/{self.r.key}"
        self.client.post(u, {"f0": "3", "by": "박서준"})
        self.assertEqual(self.r.cells.get().by, "박서준")
        r = Client().post(u, {"f0": "4", "by": "김가람"})
        self.assertNotContains(r, "대신 냄")
        cell = self.r.cells.get()
        self.assertEqual((cell.value, cell.by), ("4", "김가람"))
