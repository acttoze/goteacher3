"""E4 web/static/hwp/tablemap.js(rhwp 표 ↔ hwp.tables 짝 맞추기)를 node로 검증한다. node가 없으면 건너뛴다.

고정 입력(testdata/tablemap_*.json)은 표본 파일에서 rhwp 0.8.7 getPageControlLayout의 표 컨트롤(구조·좌표만)과
hwp.tables 요약을 뽑은 것이다. 문서 글자는 없다.
- s22: 서로 다른 부모 표(0/80/0, 0/81/0) 칸 안의 중첩 표가 rhwp stableIndex(0/0/0/0/0/0/0/0/0)가 같다 → 한 표로 합쳐지면 안 된다.
- s10: 1순위 후보가 중첩 표 → 겹침 레이어에 정확한 짝으로 나와야 한다.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1] / "web" / "static" / "hwp" / "tablemap.js"
NODE = shutil.which("node")


def run(name):
    out = subprocess.run([NODE, str(HERE / "testdata" / "run_tablemap.mjs"), str(LIB),
                          str(HERE / "testdata" / f"tablemap_{name}.json")],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout), json.loads((HERE / "testdata" / f"tablemap_{name}.json").read_text())["py"]


@unittest.skipUnless(NODE, "node 없음")
class TableMapTests(unittest.TestCase):
    def assert_parent_consistent(self, res, py):
        """짝 맞은 중첩 표의 부모(rhwp)가 짝 맞은 파이썬 부모와 같은 표여야 한다."""
        by_key = {t["key"]: t for t in res["tables"]}
        by_idx = {t["index"]: t for t in py}
        checked = 0
        for t in res["tables"]:
            if t["top"] or t["py"] is None or t["parent"] is None:
                continue
            parent_py = by_key[t["parent"]]["py"]
            if parent_py is not None:
                self.assertEqual(parent_py, by_idx[t["py"]]["parent"], t["key"])
                checked += 1
        return checked

    def test_same_stable_index_under_different_parents_stays_apart(self):
        res, py = run("s22")
        keys = [t["key"] for t in res["tables"]]
        self.assertEqual(len(keys), len(set(keys)))
        # 12쪽·13쪽(0부터 11·12)의 같은 경로 중첩 표가 부모별로 따로 있다
        same = [t for t in res["tables"] if t["key"].endswith(">0/0/0/0/0/0/0/0/0")]
        self.assertEqual({t["parent"] for t in same} >= {"0/80/0", "0/81/0"}, True)
        on12 = {f["key"]: f["py"] for f in res["pages"][11]}
        on13 = {f["key"]: f["py"] for f in res["pages"][12]}
        k12, k13 = "0/80/0>0/0/0/0/0/0/0/0/0", "0/81/0>0/0/0/0/0/0/0/0/0"
        self.assertIn(k12, on12)
        self.assertIn(k13, on13)
        self.assertNotEqual(on12[k12], on13[k13])
        # 부모가 맞는 파이썬 표와 짝(12쪽 표는 0/80/0의 짝을, 13쪽 표는 0/81/0의 짝을 부모로 둔다)
        by_key = {t["key"]: t for t in res["tables"]}
        by_idx = {t["index"]: t for t in py}
        for k in (k12, k13):
            self.assertEqual(by_idx[by_key[k]["py"]]["parent"], by_key[k.split(">")[0]]["py"])
        self.assertGreater(self.assert_parent_consistent(res, py), 0)
        # 정확히 짝 맞은 후보는 근사 목록에 또 나오지 않는다
        exact = {t["py"] for t in res["tables"] if t["py"] is not None}
        self.assertFalse(exact & {i for v in res["approx"].values() for i in v})

    def test_nested_first_candidate_shown_exactly(self):
        res, py = run("s10")
        first = next(t for t in py if t["rank"] == 0)
        self.assertTrue(first["nested"])
        self.assertIn(first["index"], {t["py"] for t in res["tables"]})
        self.assert_parent_consistent(res, py)
