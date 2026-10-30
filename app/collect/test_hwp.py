"""E6 칸 확인(한글 양식): 고른 표 → 자동 라벨 다듬기·켜고 끄기·합계 줄 빼기·반/이름 열 배정 → 모으기.

불변식
- 칸 확인 부분 화면·만들기는 회원·같은 공간·변환 끝나고 표를 고른 파일만. 다른 공간 파일 키는 404.
- POST 값은 다시 검사한다: 이 표에 없는 칸 번호·줄 번호, 그 사이 바뀐 표 위치는 거부(400, 만들지 않음).
- Field는 켠 입력 칸만. 받는 사람 줄은 늘 동학년 명부(리드 결정). 반·이름 열은 칸이 아니라 명부 값으로 채운다.
- 칸 매핑은 줄(Row.key)마다 [Field.pos 또는 unit·who, 행, 열, 단위] — hwp.fill 에 그대로 넣을 수 있다.
  표 줄이 모자라면 r=None(F1 행 추가). 합계 줄은 늘 빠진다.
- 학생 정보 칸은 기본으로 꺼지고, 켜서 보내면 서버가 거부한다(C7과 같은 규칙).
- 만든 모으기는 C5 제출·C6 합본이 그대로 동작한다(Field만 쓰므로).
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

import hwp
from hwp import engine as hwp_engine
from hwpfiles.models import SourceFile
from spaces.models import Member, School, Space

from .models import Collection


def table_doc(rows, title="학예회 반별 참가 인원 조사"):
    """결재란 + 본문 표. rows는 글자 2차원 목록(빈 문자열은 빈 칸)."""
    from hwpx.document import HwpxDocument
    d = HwpxDocument.new()
    a = d.add_table(2, 3)
    for c, x in enumerate(["담당", "부장", "교장"]):
        a.set_cell_text(0, c, x)
    d.add_paragraph(title)
    t = d.add_table(len(rows), len(rows[0]))
    for r, row in enumerate(rows):
        for c, x in enumerate(row):
            if x:
                t.set_cell_text(r, c, x)
    return d.to_bytes()


CLASS_TABLE = [["반", "담임", "참가(명)", "불참(명)", "비고"],
               ["4-1", "", "", "", ""], ["4-2", "", "", "", ""], ["4-3", "", "", "", ""],
               ["합계", "", "", "", ""]]


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class HwpFieldsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999997", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:ha"), User.objects.create(username="google:hb")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        Member.objects.create(space=cls.a, name="박서준", cls="4-2")
        Member.objects.create(space=cls.a, name="이도윤", cls="4-3")
        Member.objects.create(space=cls.a, name="최하나", cls="4-4")
        Member.objects.create(space=cls.a, name="정전담", cls="4-전담")
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, True)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)
        self.client.force_login(self.ua)

    # ---- 도우미
    def upload(self, data, name="참가.hwpx", client=None, space=None):
        space = space or self.a
        f = SimpleUploadedFile(name, data, content_type="application/octet-stream")
        r = (client or self.client).post(f"/s/{space.token}/hwp/upload?pick=1", {"file": f})
        self.assertEqual(r.status_code, 200)
        return SourceFile.objects.get(space=space, name=name)

    def picked(self, data=None, name="참가.hwpx", index=1):
        sf = self.upload(data or table_doc(CLASS_TABLE), name)
        r = self.client.post(f"/s/{self.a.token}/hwp/{sf.key}/pick", {"sec": 0, "index": index})
        self.assertEqual(r.status_code, 200)
        sf.refresh_from_db()
        return sf

    def fields_url(self, sf, space=None):
        return f"/s/{(space or self.a).token}/c/new/hwp/{sf.key}"

    def form(self, sf, **over):
        """칸 확인 화면이 보내는 값(자동 추정 그대로) + 덮어쓰기."""
        r = self.client.get(self.fields_url(sf))
        self.assertEqual(r.status_code, 200)
        f = r.context["hwpf"]
        data = {"src": "hwp", "title": "학예회 참가", "hwp": sf.key, "hwp_sent": "1", "hwp_table": f["table"],
                "hwp_items": str(len(f["items"]))}
        for it in f["items"]:
            data.update({f"item_{it['i']}_role": it["role"], f"item_{it['i']}_name": it["name"],
                         f"item_{it['i']}_kind": it["kind"]})
        for x in f["rows"]:
            if x["on"]:
                data[f"row_{x['r']}"] = "on"
        data.update(over)
        return {k: v for k, v in data.items() if v is not None}

    def create(self, sf, **over):
        return self.client.post(f"/s/{self.a.token}/c/new", self.form(sf, **over))

    # ---- 칸 확인 화면
    def test_fields_defaults_from_schema(self):
        sf = self.picked()
        r = self.client.get(self.fields_url(sf))
        f = r.context["hwpf"]
        self.assertEqual(f["mode"], "rows")
        self.assertEqual([(it["name"], it["role"], it["kind"]) for it in f["items"]],
                         [("담임", "who", "text"), ("참가(명)", "input", "number"), ("불참(명)", "input", "number"),
                          ("비고", "input", "text")])
        self.assertEqual([(x["r"], x["head"], x["total"], x["on"]) for x in f["rows"]],
                         [(1, "4-1", False, True), (2, "4-2", False, True), (3, "4-3", False, True), (4, "합계", True, False)])
        # 명부 4반(전담 빠짐) ↔ 표 3줄: 4-4는 줄 더하기
        self.assertEqual([(m["unit"], rr) for m, rr in f["pairs"]], [("4-1", 1), ("4-2", 2), ("4-3", 3), ("4-4", None)])
        self.assertEqual(f["short"], 1)
        body = r.content.decode()
        self.assertIn("받을 칸 확인", body)
        self.assertIn("합계 줄", body)
        self.assertIn("표 줄이 1개 모자라요", body)
        self.assertIn('hx-swap-oob="true"', body)  # 쪽지의 받는 칸
        self.assertIn("받는 칸: 참가(명), 불참(명), 비고", body)

    def test_fields_post_rerenders_with_choices(self):
        sf = self.picked()
        data = self.form(sf, item_3_role="off", item_1_name="참가 인원")
        r = self.client.post(self.fields_url(sf), data)
        f = r.context["hwpf"]
        self.assertEqual([it["name"] for it in f["inputs"]], ["참가 인원", "불참(명)"])
        self.assertEqual(SourceFile.objects.get(pk=sf.pk).cellmap, {})  # 다시 그리기만, 저장 안 함

    def test_fields_other_space_404(self):
        sf = self.picked()
        other = Client()
        other.force_login(self.ub)
        self.assertEqual(other.get(self.fields_url(sf, self.b)).status_code, 404)  # 내 공간 주소 + 남의 파일
        self.assertEqual(other.get(self.fields_url(sf)).status_code, 404)  # 남의 공간 주소(회원 아님)
        anon = Client()
        self.assertNotEqual(anon.get(self.fields_url(sf)).status_code, 200)

    def test_fields_unpicked_file_409(self):
        sf = self.upload(table_doc(CLASS_TABLE))
        self.assertEqual(self.client.get(self.fields_url(sf)).status_code, 409)

    # ---- 만들기
    def test_create_collection_fields_rows_cellmap(self):
        sf = self.picked()
        r = self.create(sf, outside="on")
        c = Collection.objects.get(space=self.a)
        self.assertRedirects(r, f"/s/{self.a.token}/c/{c.key}/", fetch_redirect_response=False)
        self.assertEqual((c.source, c.title, c.allow_outside), ("hwp", "학예회 참가", True))
        self.assertEqual([(f.pos, f.name, f.kind) for f in c.fields.all()],
                         [(0, "참가(명)", "number"), (1, "불참(명)", "number"), (2, "비고", "text")])
        rows = list(c.rows.all())
        self.assertEqual([(x.unit, x.who) for x in rows],
                         [("4-1", "김가람"), ("4-2", "박서준"), ("4-3", "이도윤"), ("4-4", "최하나")])
        sf.refresh_from_db()
        self.assertEqual(sf.collection, c)
        m = sf.cellmap
        self.assertEqual((m["sec"], m["index"], m["mode"], m["skip_rows"]), (0, 1, "rows", [4]))
        self.assertEqual([ln["row"] for ln in m["lines"]], [x.key for x in rows])
        self.assertEqual(m["lines"][0], {"row": rows[0].key, "r": 1,
                                         "cells": [["who", 1, 1, ""], [0, 1, 2, ""], [1, 1, 3, ""], [2, 1, 4, ""]]})
        self.assertEqual(m["lines"][3]["r"], None)  # 표 줄 모자람 → F1 행 추가
        self.assertEqual(m["lines"][3]["cells"][1], [0, None, 2, ""])
        self.assertEqual(m["cols"], [["who", 1, ""], [0, 2, ""], [1, 3, ""], [2, 4, ""]])

    def test_cellmap_fills_with_hwp_fill(self):
        """매핑만으로 hwp.fill 값을 만들 수 있다(F1 입력 형태). 합계 줄·행 머리는 그대로."""
        sf = self.picked()
        self.create(sf)
        sf.refresh_from_db()
        m = sf.cellmap
        rows = {x.key: x for x in Collection.objects.get(space=self.a).rows.all()}
        vals = {}
        for ln in m["lines"]:
            if ln["r"] is None:
                continue
            for what, r, c, _unit in ln["cells"]:
                vals[(r, c)] = rows[ln["row"]].who if what == "who" else f"{what}-{ln['r']}"
        with sf.hwpx_file().open("rb") as fh:
            data = fh.read()
        out = hwp.fill(data, m["section"], m["index"], vals)
        self.assertEqual(hwp.zip_ok(out), [])
        got = {(i["row"], i["col"]) for i in hwp.schema(out, m["section"], m["index"])["inputs"]}
        self.assertFalse(got & set(vals))  # 채운 칸은 더 이상 빈 칸이 아님

    def test_label_edit_toggle_kind(self):
        sf = self.picked()
        self.create(sf, item_1_name="참가 학생 수", item_2_role="off", item_3_kind="select")
        c = Collection.objects.get(space=self.a)
        self.assertEqual([(f.name, f.kind, f.options) for f in c.fields.all()],
                         [("참가 학생 수", "number", []), ("비고", "select", ["예", "아니오"])])
        sf.refresh_from_db()
        self.assertEqual(sf.cellmap["lines"][0]["cells"], [["who", 1, 1, ""], [0, 1, 2, ""], [1, 1, 4, ""]])

    def test_row_off_and_class_matching(self):
        rows = [CLASS_TABLE[0], ["4-3", "", "", "", ""], ["4-1", "", "", "", ""], ["소계", "", "", "", ""],
                ["4-2", "", "", "", ""], ["", "", "", "", ""]]
        sf = self.picked(table_doc(rows))
        f = self.client.get(self.fields_url(sf)).context["hwpf"]
        self.assertEqual([(m["unit"], rr) for m, rr in f["pairs"]], [("4-1", 2), ("4-2", 4), ("4-3", 1), ("4-4", 5)])
        self.assertEqual([x["r"] for x in f["rows"] if x["total"]], [3])  # 소계 줄
        self.create(sf, row_5=None)  # 빈 마지막 줄을 끔 → 4-4는 줄 더하기
        sf.refresh_from_db()
        self.assertEqual([ln["r"] for ln in sf.cellmap["lines"]], [2, 4, 1, None])
        self.assertEqual(sf.cellmap["skip_rows"], [3, 5])

    def test_spaced_head_and_filler_row(self):
        rows = [["학 년 반", "비 고"], ["4-1", ""], ["…", ""], ["(예) 4-9", "없음"]]
        sf = self.picked(table_doc(rows))
        f = self.client.get(self.fields_url(sf)).context["hwpf"]
        self.assertEqual([it["name"] for it in f["items"]], ["비고"])
        self.assertEqual([(x["head"], x["on"]) for x in f["rows"]], [("4-1", True), ("…", False)])

    def test_more_rows_than_roster_leaves_spare(self):
        rows = [["번호", "성명", "연수명"]] + [[str(i), "", ""] for i in range(1, 7)]
        sf = self.picked(table_doc(rows, "연수 이수 명단"))
        f = self.client.get(self.fields_url(sf)).context["hwpf"]
        self.assertEqual([(it["name"], it["role"]) for it in f["items"]], [("성명", "who"), ("연수명", "input")])
        self.assertEqual([rr for _, rr in f["pairs"]], [1, 2, 3, 4])
        self.assertEqual(f["spare"], 2)
        self.assertIn("남는 표 줄 2개는 비워 둬요", self.client.get(self.fields_url(sf)).content.decode())

    def test_kv_form_cells_mode(self):
        rows = [["성명", "", "소속", ""], ["연락처", "", "희망 일자", ""]]
        sf = self.picked(table_doc(rows, "강사 신청서"))
        f = self.client.get(self.fields_url(sf)).context["hwpf"]
        self.assertEqual(f["mode"], "cells")
        self.create(sf)
        c = Collection.objects.get(space=self.a)
        self.assertEqual(c.rows.count(), 4)
        sf.refresh_from_db()
        lines = sf.cellmap["lines"]
        self.assertTrue(all(ln["r"] is None and ln["cells"] == lines[0]["cells"] for ln in lines))
        self.assertEqual(sorted((r, col) for _, r, col, _ in lines[0]["cells"]), [(0, 1), (0, 3), (1, 1), (1, 3)])

    # ---- 거부
    def test_reject_out_of_range_and_stale(self):
        sf = self.picked()
        for over in ({"item_9_role": "input"}, {"row_7": "on"}, {"row_4": "on"},  # 없는 칸·없는 줄·합계 줄
                     {"hwp_items": "9"}, {"hwp_table": "0/0"}):
            r = self.create(sf, **over)
            self.assertEqual(r.status_code, 400, over)
            self.assertContains(r, "표가 바뀌었어요", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_reject_other_space_file_and_unpicked(self):
        other = Client()
        other.force_login(self.ub)
        sf_b = self.upload(table_doc(CLASS_TABLE), "남의.hwpx", client=other, space=self.b)
        r = self.client.post(f"/s/{self.a.token}/c/new", {"src": "hwp", "title": "x", "hwp": sf_b.key})
        self.assertContains(r, "한글 파일을 올려 주세요", status_code=400)
        sf = self.upload(table_doc(CLASS_TABLE), "안고름.hwpx")
        r = self.client.post(f"/s/{self.a.token}/c/new", {"src": "hwp", "title": "x", "hwp": sf.key})
        self.assertContains(r, "취합할 표를", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_reject_no_inputs_and_double_roles(self):
        sf = self.picked()
        r = self.create(sf, item_1_role="off", item_2_role="off", item_3_role="off")
        self.assertContains(r, "받을 칸을 하나 이상", status_code=400)
        r = self.create(sf, item_1_role="who")
        self.assertContains(r, "열 하나씩만", status_code=400)
        r = self.create(sf, row_1=None, row_2=None, row_3=None)
        self.assertContains(r, "표 줄을 하나 이상", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_student_info_off_by_default_and_rejected(self):
        rows = [["반", "학생 성명", "참가 여부"], ["4-1", "", ""], ["4-2", "", ""]]
        sf = self.picked(table_doc(rows))
        r = self.client.get(self.fields_url(sf))
        f = r.context["hwpf"]
        self.assertEqual([(it["name"], it["role"], it["pii"]) for it in f["items"]],
                         [("학생 성명", "off", True), ("참가 여부", "input", False)])
        self.assertContains(r, "학생 정보 · 받을 수 없어요")
        r = self.create(sf, item_0_role="input")
        self.assertContains(r, "학생 개인정보로 보이는 칸", status_code=400)
        r = self.create(sf, item_0_role="input", item_0_name="참가자")  # 이름만 바꿔도 표 머리글로 잡는다
        self.assertContains(r, "학생 개인정보로 보이는 칸", status_code=400)
        self.assertFalse(Collection.objects.exists())
        r = self.create(sf)  # 끈 채로는 만들어진다
        self.assertEqual(r.status_code, 302)
        self.assertEqual([f.name for f in Collection.objects.get().fields.all()], ["참가 여부"])

    def test_one_collection_per_file(self):
        sf = self.picked()
        self.assertEqual(self.create(sf).status_code, 302)
        r = self.create(sf)
        self.assertContains(r, "이미 모으기를 만들었어요", status_code=400)
        self.assertEqual(Collection.objects.count(), 1)

    def test_no_roster_class(self):
        sf = self.picked()
        Member.objects.filter(space=self.a).exclude(cls="4-전담").update(cls="4-전담")
        r = self.create(sf)
        self.assertContains(r, "받을 사람이나 그룹을 골라 주세요", status_code=400)

    def test_rerender_after_error_keeps_choices(self):
        sf = self.picked()
        r = self.create(sf, title="", item_1_name="참가 인원")
        self.assertContains(r, "제목을 적어 주세요", status_code=400)
        self.assertContains(r, 'value="참가 인원"', status_code=400)
        self.assertContains(r, 'id="hwpCheck"', status_code=400)

    # ---- 제출·합본(C5·C6)이 한글 양식 모으기에서도
    def test_submit_and_board_work(self):
        sf = self.picked()
        self.create(sf)
        c = Collection.objects.get(space=self.a)
        row = c.rows.get(unit="4-2")
        recv = Client()
        r = recv.get(f"/c/{c.link_token}/u/{row.key}")
        self.assertContains(r, "불참")
        r = recv.post(f"/c/{c.link_token}/u/{row.key}", {"f0": "21", "f1": "2", "f2": "없음", "by": "박서준"})
        self.assertContains(r, "제출했어요")
        self.assertEqual(sorted(row.cells.values_list("value", flat=True)), ["2", "21", "없음"])
        r = self.client.get(f"/s/{self.a.token}/c/{c.key}/")
        self.assertContains(r, "불참(명)")
        self.assertContains(r, "21")


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class HwpFieldsQaTests(HwpFieldsTests):
    """QA(E6) 경계 사례: 채운 값이 정확한 행·열에, CSRF, 다른 공간 POST, 머리글 없는 칸 이름."""

    # 부모의 테스트를 다시 돌리지 않게 가린다
    locals().update({n: None for n in dir(HwpFieldsTests) if n.startswith("test_")})

    def test_fill_puts_each_value_at_exact_cell(self):
        rows = [CLASS_TABLE[0], ["4-2", "", "", "", ""], ["4-1", "", "", "", ""], ["합계", "", "", "", ""]]
        sf = self.picked(table_doc(rows))
        self.create(sf)
        sf.refresh_from_db()
        m = sf.cellmap
        c = Collection.objects.get(space=self.a)
        by_key = {x.key: x for x in c.rows.all()}
        vals = {}
        for ln in m["lines"]:
            if ln["r"] is None:
                continue
            for what, r, col, _ in ln["cells"]:
                self.assertEqual(r, ln["r"])
                vals[(r, col)] = by_key[ln["row"]].who if what == "who" else f"{by_key[ln['row']].unit}/{what}"
        self.assertEqual(vals, {(2, 1): "김가람", (2, 2): "4-1/0", (2, 3): "4-1/1", (2, 4): "4-1/2",
                                (1, 1): "박서준", (1, 2): "4-2/0", (1, 3): "4-2/1", (1, 4): "4-2/2"})
        with sf.hwpx_file().open("rb") as fh:
            out = hwp.fill(fh.read(), m["section"], m["index"], vals)
        t = hwp_engine.find_table(hwp_engine.analyze(out), m["section"], m["index"])
        for (r, col), v in vals.items():
            self.assertEqual(" ".join((t.grid[(r, col)].text or "").split()), v, (r, col))
        self.assertEqual(" ".join(t.grid[(3, 0)].text.split()), "합계")
        self.assertFalse(any((t.grid[(3, col)].text or "").strip() for col in range(1, 5)))  # 합계 줄은 비어 있음

    def test_csrf_required(self):
        sf = self.picked()
        data = self.form(sf)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.ua)
        self.assertEqual(strict.post(self.fields_url(sf), data).status_code, 403)
        self.assertEqual(strict.post(f"/s/{self.a.token}/c/new", data).status_code, 403)
        self.assertFalse(Collection.objects.exists())

    def test_fields_post_other_space_404(self):
        sf = self.picked()
        other = Client()
        other.force_login(self.ub)
        data = self.form(sf)
        self.assertEqual(other.post(self.fields_url(sf, self.b), data).status_code, 404)
        self.assertEqual(other.post(self.fields_url(sf), data).status_code, 404)
        r = other.post(f"/s/{self.b.token}/c/new", dict(data, hwp=sf.key))  # 남의 파일 키를 내 공간에서
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Collection.objects.exists())

    def test_unlabeled_kv_cell_name_is_human(self):
        rows = [["", "", "소속", ""], ["연락처", "", "", ""]]
        sf = self.picked(table_doc(rows, "신청서"))
        f = self.client.get(self.fields_url(sf)).context["hwpf"]
        self.assertFalse([it["name"] for it in f["items"] if it["name"].startswith("(")])
