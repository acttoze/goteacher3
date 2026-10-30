"""F1 채우기: 모으기 제출 값 → 칸 매핑으로 병합 → hwp.fill(+행 추가) → 올린 형식 + hwpx → 재검증 → 결과 파일.

불변식
- 반·이름(명부 값)은 표 줄이 있으면 늘 채우고, 낸 값은 있는 칸만. 단위 칸은 '3' → '3명'.
- 표 줄이 모자란 반(r=None)·명부 밖 줄은 낸 값이 있을 때만 표 끝(합계 줄 위)에 줄을 더해 칸 틀로 채운다.
- 머리글 없는 신청서형은 낸 선생님마다 한 부씩 zip.
- 같은 칸 주소에 다른 값이 오면 먼저 온 줄 값을 두고 주소만 경고.
- 재검증에 실패하면 결과 파일을 남기지 않는다. 오류·로그에는 행·열 주소만(값 없음).
- 회원·같은 공간만, 캐시 금지, 파일 이름 RFC 5987, 회원당 진행 중 한글 작업 2건.
"""
import io
import os
import shutil
import tempfile
import zipfile
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

import hwp
from collect.models import Cell, Collection, Row
from collect.recipients import add_outside_row
from collect.test_hwp import CLASS_TABLE, table_doc
from hwp import engine as E
from spaces.models import Member, School, Space

from . import merge
from .models import ResultFile, SourceFile

SECRET = "비밀값-제출내용"


def texts(hx, section, index):
    t = E.find_table(E.analyze(hx), section, index)
    return t, {(c.row, c.col): c.text for c in t.cells}


class MergeTests(TestCase):
    """병합 규칙(DB 없이)."""

    def row(self, key, unit="4-1", who="김가람"):
        return SimpleNamespace(key=key, unit=unit, who=who)

    def test_rows_unit_suffix_names_and_empty(self):
        cm = {"mode": "rows", "lines": [{"row": "a", "r": 1, "cells": [["who", 1, 1, ""], [0, 1, 2, "명"], [1, 1, 3, ""]]},
                                        {"row": "b", "r": 2, "cells": [["who", 2, 1, ""], [0, 2, 2, "명"], [1, 2, 3, ""]]}]}
        p = merge.plan(cm, [self.row("a"), self.row("b", "4-2", "박서준")], {"a": {0: "3", 1: " 줄\n바꿈 "}})
        self.assertEqual(p.values, {(1, 1): "김가람", (1, 2): "3명", (1, 3): "줄 바꿈", (2, 1): "박서준"})
        self.assertEqual((p.extra, p.conflicts), ([], []))
        self.assertEqual(merge.text(0, "명", self.row("a"), {0: "5명"}), "5명")  # 단위 두 번 안 붙임

    def test_short_rows_and_outside_become_extra_only_with_values(self):
        cm = {"mode": "rows", "lines": [{"row": "a", "r": None, "cells": [[0, None, 2, ""]]},
                                        {"row": "b", "r": None, "cells": [[0, None, 2, ""]]}],
              "cols": [["who", 1, ""], [0, 2, ""]]}
        rows = [self.row("a"), self.row("b"), self.row("x", "명부 밖", "외부")]
        p = merge.plan(cm, rows, {"a": {0: "1"}, "x": {0: "2"}})
        self.assertEqual([r.key for r in p.extra], ["a", "x"])
        items = merge.extra_values(cm, p.extra, 5, {"a": {0: "1"}, "x": {0: "2"}})
        self.assertEqual(items, [(5, 1, "who", "김가람"), (5, 2, 0, "1"), (6, 1, "who", "외부"), (6, 2, 0, "2")])
        # 더한 줄 6에서 1·2열이 한 칸으로 병합돼 있으면: 이름은 그 칸에, 같은 칸을 덮는 낸 값은 못 넣음(1)
        inputs = [{"row": 5, "col": 1, "colSpan": 1}, {"row": 5, "col": 2, "colSpan": 1}, {"row": 6, "col": 1, "colSpan": 2}]
        self.assertEqual(merge.place(items, inputs),
                         ({(5, 1): "김가람", (5, 2): "1", (6, 1): "외부"}, 1))
        self.assertEqual(merge.head_value("3", 1, rows[0]), "5")
        self.assertEqual(merge.head_value("4-3", 0, rows[0]), "4-1")

    def test_conflict_keeps_first_and_reports_address(self):
        cm = {"mode": "rows", "lines": [{"row": "a", "r": 1, "cells": [[0, 1, 2, ""]]},
                                        {"row": "b", "r": 1, "cells": [[0, 1, 2, ""]]},
                                        {"row": "c", "r": 1, "cells": [[0, 1, 2, ""]]}]}
        p = merge.plan(cm, [self.row("a"), self.row("b"), self.row("c")], {"a": {0: "1"}, "b": {0: "2"}, "c": {0: "1"}})
        self.assertEqual((p.values, p.conflicts), ({(1, 2): "1"}, [(1, 2)]))

    def test_cells_mode_one_copy_per_submitter(self):
        cm = {"mode": "cells", "lines": [{"row": "a", "r": None, "cells": [["who", 0, 1, ""], [0, 1, 1, ""]]},
                                         {"row": "b", "r": None, "cells": [["who", 0, 1, ""], [0, 1, 1, ""]]}]}
        rows = [self.row("a"), self.row("b", who="박서준"), self.row("x", "명부 밖", "외부")]
        p = merge.plan(cm, rows, {"b": {0: "연수"}, "x": {0: "다른 연수"}})
        self.assertEqual([(r.key, v) for r, v in p.copies],
                         [("b", {(0, 1): "박서준", (1, 1): "연수"}), ("x", {(0, 1): "외부", (1, 1): "다른 연수"})])


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class FillTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999996", name="채움초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:fa"), User.objects.create(username="google:fb")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        Member.objects.create(space=cls.a, name="박서준", cls="4-2")
        Member.objects.create(space=cls.a, name="이도윤", cls="4-3")
        Member.objects.create(space=cls.a, name="최하나", cls="4-4")
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        o = override_settings(MEDIA_ROOT=self.media)
        o.enable()
        self.addCleanup(o.disable)
        self.client.force_login(self.ua)

    # ---- 도우미: E5 고르기 → E6 칸 확인 기본값 → 만들기
    def make(self, data, name="참가.hwpx", index=1, **over):
        f = SimpleUploadedFile(name, data, content_type="application/octet-stream")
        self.assertEqual(self.client.post(f"/s/{self.a.token}/hwp/upload?pick=1", {"file": f}).status_code, 200)
        sf = SourceFile.objects.get(space=self.a, name=name)
        self.assertEqual(self.client.post(f"/s/{self.a.token}/hwp/{sf.key}/pick", {"sec": 0, "index": index}).status_code, 200)
        f = self.client.get(f"/s/{self.a.token}/c/new/hwp/{sf.key}").context["hwpf"]
        post = {"src": "hwp", "title": "채우기", "hwp": sf.key, "hwp_sent": "1", "hwp_table": f["table"],
                "hwp_items": str(len(f["items"]))}
        for it in f["items"]:
            post.update({f"item_{it['i']}_role": it["role"], f"item_{it['i']}_name": it["name"],
                         f"item_{it['i']}_kind": it["kind"]})
        for x in f["rows"]:
            if x["on"]:
                post[f"row_{x['r']}"] = "on"
        post.update(over)
        self.assertEqual(self.client.post(f"/s/{self.a.token}/c/new", post).status_code, 302)
        sf.refresh_from_db()
        return sf, sf.collection

    def submit(self, row, **vals):
        for f in row.collection.fields.all():
            if f.name in vals:
                Cell.objects.update_or_create(row=row, field=f, defaults={"value": vals[f.name], "by": row.who})

    def url(self, c, space=None):
        return f"/s/{(space or self.a).token}/c/{c.key}/hwp"

    def run_fill(self, c, client=None):
        r = (client or self.client).post(self.url(c))
        self.assertEqual(r.status_code, 200)
        return ResultFile.objects.filter(collection=c).latest("created_at")

    def download(self, rf, which):
        r = self.client.get(f"/s/{self.a.token}/hwp/out/{rf.key}/{which}")
        self.assertEqual(r.status_code, 200)
        return r, b"".join(r.streaming_content)

    # ---- 줄 모드: 병합·행 추가·명부 밖 줄
    def test_rows_merge_add_row_and_outside(self):
        sf, c = self.make(table_doc(CLASS_TABLE), outside="on")
        rows = list(c.rows.all())   # 4-1·4-2·4-3 은 표 줄, 4-4 는 r=None
        self.submit(rows[0], **{"참가(명)": "21", "비고": "없음"})
        self.submit(rows[3], **{"참가(명)": "19", "불참(명)": "1"})
        out_row = add_outside_row(c, "강전담")
        self.submit(out_row, **{"참가(명)": "2"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.fmt, rf.bundle, rf.added, rf.conflicts), ("done", "hwpx", False, 2, []))
        self.assertFalse(rf.file)                 # hwpx 원본: 올린 형식 = hwpx 하나
        r, data = self.download(rf, "hwpx")
        t, m = texts(data, sf.cellmap["section"], 1)
        self.assertEqual(t.rows, 7)
        self.assertEqual([m[(i, 0)] for i in range(7)], ["반", "4-1", "4-2", "4-3", "4-4", "명부 밖", "합계"])
        self.assertEqual([m[(1, c)] for c in range(1, 5)], ["김가람", "21", "", "없음"])
        self.assertEqual([m[(2, c)] for c in range(1, 5)], ["박서준", "", "", ""])  # 안 낸 반: 이름만
        self.assertEqual([m[(4, c)] for c in range(1, 5)], ["최하나", "19", "1", ""])
        self.assertEqual([m[(5, c)] for c in range(1, 5)], ["강전담", "2", "", ""])
        self.assertEqual(hwp.zip_ok(data), [])
        self.assertIn("filename*=utf-8''", r["Content-Disposition"])
        self.assertIn("%ED%95%A9%EB%B3%B8.hwpx", r["Content-Disposition"])   # 참가_합본.hwpx
        self.assertIn("no-store", r["Cache-Control"])

    def test_hwp_upload_gives_hwp_and_hwpx(self):
        hx = table_doc(CLASS_TABLE)
        sec = hwp.candidates(hx)[0]["section"]
        sf, c = self.make(hwp.to_hwp(hx, sec, 1, {}), name="참가.hwp")
        self.submit(c.rows.first(), **{"참가(명)": "7"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.fmt), ("done", "hwp"))
        r, data = self.download(rf, "file")
        self.assertEqual(r["Content-Type"], "application/x-hwp")
        self.assertIn("%ED%95%A9%EB%B3%B8.hwp", r["Content-Disposition"])
        back, kind, _ = hwp.to_hwpx(data)
        self.assertEqual(kind, "hwp")
        self.assertEqual(texts(back, sf.cellmap["section"], 1)[1][(1, 2)], "7")
        _, data = self.download(rf, "hwpx")
        self.assertEqual(texts(data, sf.cellmap["section"], 1)[1][(1, 2)], "7")
        self.assertEqual(rf.stored, os.path.getsize(rf.file.path) + os.path.getsize(rf.hwpx.path))

    def test_output_setting_switches_hwp_to_hwpx(self):
        hx = table_doc(CLASS_TABLE)
        sf, c = self.make(hwp.to_hwp(hx, hwp.candidates(hx)[0]["section"], 1, {}), name="참가.hwp")
        with mock.patch.dict(os.environ, {"GT_HWP_OUTPUT": "hwpx"}):
            rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.fmt, bool(rf.file), bool(rf.hwpx)), ("done", "hwpx", False, True))
        self.assertEqual(self.client.get(f"/s/{self.a.token}/hwp/out/{rf.key}/file").status_code, 404)

    # ---- 신청서형: 선생님마다 한 부(zip)
    def test_form_type_zip_per_teacher(self):
        sf, c = self.make(table_doc([["연수명", ""], ["희망 일자", ""]], title="연수 신청서"))
        self.assertEqual(sf.cellmap["mode"], "cells")
        rows = list(c.rows.all())
        self.submit(rows[0], **{"연수명": "글쓰기", "희망 일자": "11월"})
        self.submit(rows[2], **{"연수명": "코딩"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.bundle, rf.copies), ("done", True, 2))
        r, data = self.download(rf, "hwpx")
        self.assertEqual(r["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            self.assertEqual(names, ["01_4-1_김가람.hwpx", "02_4-3_이도윤.hwpx"])
            got = [texts(z.read(n), sf.cellmap["section"], 1)[1] for n in names]
        self.assertEqual([(g[(0, 1)], g[(1, 1)]) for g in got], [("글쓰기", "11월"), ("코딩", "")])

    def test_form_type_without_values_fails_politely(self):
        _sf, c = self.make(table_doc([["연수명", ""], ["희망 일자", ""]], title="연수 신청서"))
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.error), ("failed", "NoValues"))
        self.assertContains(self.client.get(self.url(c)), "아직 낸 선생님이 없어요")

    # ---- 충돌
    def test_conflict_warning_addresses_only(self):
        sf, c = self.make(table_doc(CLASS_TABLE))
        lines = sf.cellmap["lines"]
        lines[1]["cells"] = [[0, 1, 2, ""]]        # 둘째 줄을 첫 줄과 같은 칸에 이음(매핑 겹침)
        SourceFile.objects.filter(pk=sf.pk).update(cellmap={**sf.cellmap, "lines": lines})
        rows = list(c.rows.all())
        self.submit(rows[0], **{"참가(명)": "1"})
        self.submit(rows[1], **{"참가(명)": SECRET})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.conflicts), ("done", [[1, 2]]))
        page = self.client.get(self.url(c)).content.decode()
        self.assertIn("같은 칸에 값이 둘", page)
        self.assertNotIn(SECRET, page)

    # ---- 검증 실패면 결과를 남기지 않음, 로그·오류에 값 없음
    def test_verify_failure_leaves_no_file_and_no_values(self):
        sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"참가(명)": "3", "비고": SECRET})
        real = hwp.verify

        def broken(*a, **kw):
            return {**real(*a, **kw), "ok": False, "values_bad": [(1, 4)]}
        with mock.patch("hwpfiles.tasks.hwp.verify", broken), self.assertLogs("hwpfiles.tasks", "WARNING") as logs:
            rf = self.run_fill(c)
        rf.refresh_from_db()
        self.assertEqual((rf.status, rf.error, rf.problems, rf.stored, bool(rf.file), bool(rf.hwpx)),
                         ("failed", "FillError", [[1, 4]], 0, False, False))
        self.assertNotIn(SECRET, "".join(logs.output))
        on_disk = [f for _, _, fs in os.walk(self.media) for f in fs if ".out." in f]
        self.assertEqual(on_disk, [])
        page = self.client.get(self.url(c)).content.decode()
        self.assertIn("2행 5열", page)
        self.assertNotIn(SECRET, page)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/hwp/out/{rf.key}/hwpx").status_code, 404)

    def test_add_rows_failure_fills_rest_and_tells(self):
        """줄을 더할 수 없는 표(중첩 표 등): 있는 줄은 채워 내주고, 못 넣은 줄 수를 알린다(전체 실패로 만들지 않음)."""
        sf, c = self.make(table_doc(CLASS_TABLE))
        rows = list(c.rows.all())
        self.submit(rows[0], **{"참가(명)": "5"})
        self.submit(rows[3], **{"참가(명)": SECRET})
        err = hwp.FillError("x", [((sf.cellmap["section"], 1, 3, None), "insert row skipped")])
        with mock.patch("hwpfiles.tasks.hwp.add_rows", side_effect=err), self.assertLogs("hwpfiles.tasks", "WARNING") as logs:
            rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.added, rf.left_out), ("done", 0, 1))
        _, data = self.download(rf, "hwpx")
        t, m = texts(data, sf.cellmap["section"], 1)
        self.assertEqual((t.rows, m[(1, 2)]), (5, "5"))
        self.assertNotIn(SECRET, "".join(logs.output))
        self.assertContains(self.client.get(self.url(c)), "1줄은 넣지 못했어요")

    def test_prefilled_row_not_paired_value_goes_to_added_row(self):
        """원래 글자가 있는 줄(부분 채움 표)은 명부와 짝짓지 않는다(E6). 그 반 값은 표 끝에 더한 줄로 — 버리는 값 0."""
        doc = table_doc([["반", "담임", "참가(명)"], ["4-1", "", ""], ["4-2", "", "휴업"], ["4-3", "", ""]])
        sf, c = self.make(doc)
        self.assertEqual([ln["r"] for ln in sf.cellmap["lines"]], [1, None, 3, None])
        rows = list(c.rows.all())
        self.submit(rows[1], **{"참가(명)": "9"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.dropped, rf.added), ("done", 0, 1))
        _, data = self.download(rf, "hwpx")
        m = texts(data, sf.cellmap["section"], 1)[1]
        self.assertEqual((m[(2, 2)], m[(4, 0)], m[(4, 1)], m[(4, 2)]), ("휴업", "4-2", "박서준", "9"))

    # ---- 다시 만들기: 마지막 결과만 남김
    def test_rerun_replaces_old_result(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"참가(명)": "3"})
        old = self.run_fill(c)
        path = old.hwpx.path
        new = self.run_fill(c)
        self.assertNotEqual(old.key, new.key)
        self.assertEqual(list(ResultFile.objects.filter(collection=c)), [new])
        self.assertFalse(os.path.exists(path))

    # ---- 격리·회원만·캐시 금지
    def test_isolation_and_members_only(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"참가(명)": "3"})
        rf = self.run_fill(c)
        other = Client()
        other.force_login(self.ub)
        self.assertEqual(other.post(self.url(c)).status_code, 404)                 # 남의 공간 주소
        self.assertEqual(other.post(self.url(c, self.b)).status_code, 404)         # 내 공간 주소로 남의 모으기
        self.assertEqual(other.get(f"/s/{self.b.token}/hwp/out/{rf.key}/hwpx").status_code, 404)
        self.assertEqual(other.get(f"/s/{self.a.token}/hwp/out/{rf.key}/hwpx").status_code, 404)
        self.assertNotEqual(Client().get(f"/s/{self.a.token}/hwp/out/{rf.key}/hwpx").status_code, 200)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/hwp/out/{rf.key}/x").status_code, 404)
        self.assertEqual(ResultFile.objects.count(), 1)
        r = self.client.get(self.url(c))
        self.assertIn("no-store", r["Cache-Control"])

    def test_not_hwp_collection_404(self):
        c = Collection.objects.create(space=self.a, title="그냥", source=Collection.Source.SHEET)
        self.assertEqual(self.client.post(self.url(c)).status_code, 404)
        self.assertNotContains(self.client.get(f"/s/{self.a.token}/c/{c.key}/"), "원본 한글 양식에 채워 받기")

    # ---- 진행 중 상한(E2와 같은 2건, 변환 + 채우기)
    def test_in_flight_limit(self):
        sf, c = self.make(table_doc(CLASS_TABLE))
        _sf2, c2 = self.make(table_doc(CLASS_TABLE), name="둘.hwpx")
        for _ in range(2):   # 진행 중 2건(어느 모으기든 회원 몫으로 센다)
            ResultFile.objects.create(space=self.a, source=sf, collection=c, created_by=self.ma, fmt="hwpx")
        self.assertEqual(self.client.post(self.url(c)).status_code, 200)    # 그 모으기는 진행 중: 상태만
        r = self.client.post(self.url(c2))
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "앞서 맡긴 한글 작업", status_code=429)
        self.assertEqual(ResultFile.objects.filter(collection=c2).count(), 0)
        self.assertEqual(ResultFile.objects.count(), 2)

    def test_detail_page_shows_button(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        r = self.client.get(f"/s/{self.a.token}/c/{c.key}/")
        self.assertContains(r, "원본 한글 양식에 채워 받기")
        self.assertContains(r, f'hx-post="/s/{self.a.token}/c/{c.key}/hwp"')

    def test_rows_in_db_untouched_by_fill(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        before = list(Row.objects.filter(collection=c).values_list("key", "pos"))
        self.run_fill(c)
        self.assertEqual(list(Row.objects.filter(collection=c).values_list("key", "pos")), before)


FORM_DOC = [["연수명", ""], ["희망 일자", ""]]


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class ReviewFixTests(TestCase):
    """리드 검증·보안 권고 반영: 신청서형 누적 용량·부수 상한, 동시 요청 상한, 예상 밖 예외 문구, 특수 문자 왕복,
    기준 줄 아래 줄 밀림."""

    setUpTestData = classmethod(FillTaskTests.setUpTestData.__func__)
    setUp = FillTaskTests.setUp
    make, submit, url, run_fill, download = (FillTaskTests.make, FillTaskTests.submit, FillTaskTests.url,
                                            FillTaskTests.run_fill, FillTaskTests.download)

    def form_with_values(self):
        sf, c = self.make(table_doc(FORM_DOC, title="연수 신청서"))
        for row in c.rows.all():
            self.submit(row, **{"연수명": "글쓰기"})
        return sf, c

    def test_bundle_stops_when_room_runs_out(self):
        _sf, c = self.form_with_values()
        built = []
        real = __import__("hwpfiles.tasks", fromlist=["_one"])._one

        def counting(*a, **kw):
            built.append(1)
            return real(*a, **kw)
        with override_settings(HWP_SPACE_QUOTA=SourceFile.used_bytes(self.a) + 1500), \
                mock.patch("hwpfiles.tasks._one", counting), self.assertLogs("hwpfiles.tasks", "WARNING"):
            rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.error, rf.stored), ("failed", "Quota", 0))
        self.assertLess(len(built), c.rows.count())     # 다 만들기 전에 멈춤
        self.assertEqual([f for _, _, fs in os.walk(self.media) for f in fs if ".out." in f], [])

    def test_bundle_copy_limit(self):
        _sf, c = self.form_with_values()
        with mock.patch("hwpfiles.tasks.MAX_COPIES", 2), self.assertLogs("hwpfiles.tasks", "WARNING"):
            rf = self.run_fill(c)
            self.assertContains(self.client.get(self.url(c)), "한 번에 2부까지")
        self.assertEqual((rf.status, rf.error), ("failed", "TooManyCopies"))

    def test_in_flight_counted_under_member_lock(self):
        """세기와 만들기가 회원 줄 잠금(select_for_update) 안에서 일어난다."""
        _sf, c = self.make(table_doc(CLASS_TABLE))
        from django.db.models.query import QuerySet
        real, seen = QuerySet.select_for_update, []

        def spy(qs, *a, **kw):
            seen.append(qs.model.__name__)
            return real(qs, *a, **kw)
        with mock.patch.object(QuerySet, "select_for_update", spy):
            self.run_fill(c)
        self.assertIn("Member", seen)

    def test_unexpected_error_reraised_without_message(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"비고": SECRET})
        boom = ValueError(f"문서 글자 {SECRET}")
        with mock.patch("hwpfiles.tasks.hwp.fill", side_effect=boom), self.assertLogs("hwpfiles.tasks", "WARNING"), \
                self.assertRaises(RuntimeError) as cm:
            self.client.post(self.url(c))
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertIn("ValueError", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)
        self.assertTrue(cm.exception.__suppress_context__)
        rf = ResultFile.objects.get(collection=c)
        self.assertEqual((rf.status, rf.error), ("failed", "ValueError"))

    def test_special_characters_round_trip_hwp_and_hwpx(self):
        hx = table_doc(CLASS_TABLE)
        sf, c = self.make(hwp.to_hwp(hx, hwp.candidates(hx)[0]["section"], 1, {}), name="참가.hwp")
        raw = '<b>&amp; ]]> "큰따옴" \'작은\'\x01\x07\t끝'
        self.submit(c.rows.first(), **{"비고": raw})
        rf = self.run_fill(c)
        self.assertEqual(rf.status, "done")
        want = '<b>&amp; ]]> "큰따옴" \'작은\' 끝'
        for which in ("hwpx", "file"):
            _, data = self.download(rf, which)
            hx2 = hwp.to_hwpx(data)[0]
            self.assertEqual(hwp.zip_ok(hx2), [])
            self.assertEqual(texts(hx2, sf.cellmap["section"], 1)[1][(1, 4)], want)

    def test_rows_below_ref_shift_down(self):
        """칸이 다 있는 마지막 줄 아래에 줄을 더한다. 그 아래 줄(원래 글자가 있는 줄·합계)은 밀린다."""
        doc = table_doc([["반", "담임", "참가(명)"], ["4-1", "", ""], ["4-2", "", ""], ["4-3", "", "휴업"],
                         ["합계", "", ""]])
        sf, c = self.make(doc)
        rows = list(c.rows.all())
        self.submit(rows[0], **{"참가(명)": "1"})
        self.submit(rows[2], **{"참가(명)": "3"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.added, rf.dropped), ("done", 1, 0))   # 4-3 반은 휴업 줄 대신 더한 줄로
        _, data = self.download(rf, "hwpx")
        t, m = texts(data, sf.cellmap["section"], 1)
        self.assertEqual([m[(r, 0)] for r in range(t.rows)], ["반", "4-1", "4-2", "4-3", "4-3", "합계"])
        self.assertEqual([m[(r, 2)] for r in range(t.rows)], ["참가(명)", "1", "", "3", "휴업", ""])

    def test_block_rows_added_as_blocks_nothing_dropped(self):
        """줄 하나가 2칸 높이(세로 병합)인 표: 묶음째 더해 낸 값을 모두 넣는다(넣지 못한 값 0)."""
        from hwp.tests.test_rows import block_doc
        sf, c = self.make(block_doc(), index=0)
        rows = list(c.rows.all())
        self.assertEqual([ln["r"] for ln in sf.cellmap["lines"]], [1, 3, None, None])
        self.submit(rows[0], **{"인원": "5", "비고": "가"})
        self.submit(rows[3], **{"인원": "7", "비고": "나"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.added, rf.dropped, rf.left_out), ("done", 1, 0, 0))
        _, data = self.download(rf, "hwpx")
        t, m = texts(data, sf.cellmap["section"], 0)
        self.assertEqual(t.rows, 8)
        self.assertEqual([m[(r, 0)] for r in (1, 3, 5, 7)], ["4-1", "4-2", "4-4", "합계"])
        self.assertEqual((m[(1, 2)], m[(1, 3)], m[(5, 1)], m[(5, 2)], m[(5, 3)], m[(6, 3)]),
                         ("5", "가", "최하나", "7", "나", ""))


@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class QAFillEdgeTests(TestCase):
    """QA(F1) 추가 경계: 결재란·다른 칸 불변(hwp·hwpx 둘 다), hwp 신청서형 zip, 위험한 이름, 작업 시점 저장 상한,
    성공 로그에 값 없음, 로그인 없는 받는 사람은 결과에 못 닿음."""

    setUpTestData = classmethod(FillTaskTests.setUpTestData.__func__)
    setUp = FillTaskTests.setUp
    make, submit, url, run_fill, download = (FillTaskTests.make, FillTaskTests.submit, FillTaskTests.url,
                                            FillTaskTests.run_fill, FillTaskTests.download)

    def all_texts(self, hx):
        return [(t.section, t.index, [(c.row, c.col, c.text) for c in t.cells]) for t in E.analyze(hx)]

    def test_hwp_rows_add_row_other_parts_unchanged(self):
        hx = table_doc(CLASS_TABLE)
        sec = hwp.candidates(hx)[0]["section"]
        src_hwp = hwp.to_hwp(hx, sec, 1, {})
        sf, c = self.make(src_hwp, name="참가.hwp", outside="on")
        base = hwp.to_hwpx(src_hwp)[0]
        rows = list(c.rows.all())
        tricky = '<a href="x">&amp; \'따옴\'</a>'
        self.submit(rows[1], **{"참가(명)": "12", "비고": tricky})
        self.submit(rows[3], **{"불참(명)": "4"})       # 표 줄 없는 4-4 → 줄 더함
        self.submit(add_outside_row(c, "강전담"), **{"비고": "보결"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.fmt, rf.added), ("done", "hwp", 2))
        _, body = self.download(rf, "file")
        _, xbody = self.download(rf, "hwpx")
        before = {(t.section, t.index): t for t in E.analyze(base)}
        for hxo in (hwp.to_hwpx(body)[0], xbody):
            after = {(t.section, t.index): t for t in E.analyze(hxo)}
            self.assertEqual(set(after), set(before))
            for k, t in before.items():          # 결재란(표 0) 글자 그대로
                if k[1] != 1:
                    self.assertEqual([c.text for c in after[k].cells], [c.text for c in t.cells])
            _t, m = texts(hxo, sec, 1)
            self.assertEqual(_t.rows, 7)
            self.assertEqual([m[(0, i)] for i in range(5)], CLASS_TABLE[0])     # 머리글 그대로
            self.assertEqual([m[(2, i)] for i in range(5)], ["4-2", "박서준", "12", "", tricky])
            self.assertEqual([m[(1, i)] for i in range(5)], ["4-1", "김가람", "", "", ""])
            self.assertEqual([m[(4, i)] for i in range(5)], ["4-4", "최하나", "", "4", ""])
            self.assertEqual([m[(5, i)] for i in range(5)], ["명부 밖", "강전담", "", "", "보결"])
            self.assertEqual(m[(6, 0)], "합계")
            self.assertEqual(hwp.zip_ok(hxo), [])

    def test_hwp_form_type_zip_per_teacher_and_safe_names(self):
        hx = table_doc([["연수명", ""], ["희망 일자", ""]], title="연수 신청서")
        sec = hwp.candidates(hx)[0]["section"]
        sf, c = self.make(hwp.to_hwp(hx, sec, 1, {}), name="신청.hwp", outside="on")
        self.assertEqual(sf.cellmap["mode"], "cells")
        rows = list(c.rows.all())
        self.submit(rows[1], **{"연수명": "글쓰기"})
        self.submit(add_outside_row(c, '../..\\x:*?"<>|y'), **{"연수명": "코딩", "희망 일자": "12월"})
        rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.bundle, rf.copies, rf.fmt), ("done", True, 2, "hwp"))
        r, zbody = self.download(rf, "file")
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertIn(".zip", r["Content-Disposition"])
        _, xz = self.download(rf, "hwpx")
        want = [("글쓰기", ""), ("코딩", "12월")]
        for data, ext in ((zbody, "hwp"), (xz, "hwpx")):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                self.assertEqual(len(names), 2)
                for n in names:
                    self.assertTrue(n.endswith("." + ext))
                    self.assertFalse(any(ch in n for ch in '/\\:*?"<>|'), n)
                    self.assertNotIn("..", n.rsplit(".", 1)[0].split("_", 1)[0])
                got = []
                for n in names:
                    one = z.read(n)
                    hxo = hwp.to_hwpx(one)[0] if ext == "hwp" else one
                    if ext == "hwp":
                        self.assertEqual(hwp.to_hwpx(one)[1], "hwp")
                    m = texts(hxo, sec, 1)[1]
                    got.append((m[(0, 1)], m[(1, 1)]))
                    self.assertEqual((m[(0, 0)], m[(1, 0)]), ("연수명", "희망 일자"))
                    self.assertEqual([x.text for x in E.find_table(E.analyze(hxo), sec, 0).cells],
                                     ["담당", "부장", "교장", "", "", ""])
                self.assertEqual(got, want)

    def test_quota_exceeded_at_fill_time_leaves_nothing(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"참가(명)": "3"})
        used = SourceFile.used_bytes(self.a)
        with override_settings(HWP_SPACE_QUOTA=used + 10):
            rf = self.run_fill(c)
        self.assertEqual((rf.status, rf.error, rf.stored, bool(rf.hwpx)), ("failed", "Quota", 0, False))
        self.assertEqual([f for _, _, fs in os.walk(self.media) for f in fs if ".out." in f], [])
        self.assertContains(self.client.get(self.url(c)), "저장 공간이 가득")

    def test_success_log_and_pages_have_no_values(self):
        sf, c = self.make(table_doc(CLASS_TABLE), outside="on")
        self.submit(c.rows.first(), **{"비고": SECRET})
        self.submit(add_outside_row(c, "강전담"), **{"비고": SECRET + "2"})
        with self.assertLogs("hwpfiles", "INFO") as logs:
            rf = self.run_fill(c)
        self.assertEqual(rf.status, "done")
        self.assertNotIn(SECRET, "".join(logs.output))
        self.assertNotIn("강전담", "".join(logs.output))
        part = self.client.get(self.url(c)).content.decode()
        self.assertNotIn(SECRET, part)
        self.assertNotIn(SECRET, rf.problems.__repr__() + repr(rf.conflicts))

    def test_recipient_link_without_login_cannot_reach_results(self):
        _sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"참가(명)": "3"})
        rf = self.run_fill(c)
        anon = Client()
        self.assertEqual(anon.get(f"/c/{c.link_token}").status_code in (200, 301, 302), True)   # 받는 링크는 열림
        for u in (self.url(c), f"/s/{self.a.token}/hwp/out/{rf.key}/hwpx", f"/c/{c.link_token}/hwp",
                  f"/c/{c.link_token}/hwp/out/{rf.key}/hwpx"):
            r = anon.get(u)
            self.assertNotEqual(r.status_code, 200, u)
            self.assertFalse(getattr(r, "streaming", False), u)
        self.assertNotEqual(anon.post(self.url(c)).status_code, 200)
        self.assertEqual(ResultFile.objects.count(), 1)

    def test_other_collection_result_key_in_other_space(self):
        """B 공간 회원이 자기 공간에 결과를 만든 뒤, A 결과 key 를 자기 공간 주소에 끼워도 404."""
        _sf, c = self.make(table_doc(CLASS_TABLE))
        self.submit(c.rows.first(), **{"참가(명)": "3"})
        ra = self.run_fill(c)
        other = Client()
        other.force_login(self.ub)
        for which in ("hwpx", "file"):
            self.assertEqual(other.get(f"/s/{self.b.token}/hwp/out/{ra.key}/{which}").status_code, 404)
        self.assertEqual(other.get(f"/s/{self.b.token}/c/{c.key}/hwp").status_code, 404)
