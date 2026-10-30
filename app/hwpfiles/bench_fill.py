"""F1 종단 벤치: 표본 한글 파일마다 올리기 → E5 1순위 표 확정 → E6 칸 확인 기본값으로 만들기 → 제출 화면으로
임의 값 제출(명부 6반 + 명부 밖 1줄) → '한글 파일로 받기' → 내려받아 따로 검증.

    cd app && GT_BENCH_DIR=<표본폴더> GT_BENCH_ORDER=<순서 목록> GT_BENCH_N=50 \\
        SECRET_KEY=x uv run python manage.py test hwpfiles.bench_fill

test*.py 가 아니라 평소 테스트에는 안 돈다. 표본은 신뢰하지 않는 입력이라 저장소 밖 복사본 폴더를 넘긴다.
결과에는 파일 순번·단계·시간·실패 종류만 찍는다(문서 내용·값 없음).
따로 검증: hwpx 는 zip_ok + 고른 표 칸 글자에 낸 값(단위 붙임)이 모두 들어 있는지, hwp 는 다시 읽어 같은 검사.
"""
import io
import json
import os
import random
import shutil
import tempfile
import time
import unittest
import zipfile
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

import hwp
from collect.models import Field
from hwp import engine as E
from spaces.models import Member, School, Space

from .models import ResultFile, SourceFile

DIR = os.environ.get("GT_BENCH_DIR", "")
CLASSES = 6


def _value(f, rnd):
    if f.kind == Field.Kind.NUMBER:
        return str(rnd.randint(0, 40))
    if f.kind == Field.Kind.SELECT:
        return rnd.choice(f.options)
    if f.kind == Field.Kind.DATE:
        return f"2026-11-{rnd.randint(1, 28):02d}"
    if f.kind == Field.Kind.URL:
        return "https://example.com/a"
    return f"값{rnd.randint(1, 999)}"


def _texts(hx, section, index):
    return [c.text for c in E.find_table(E.analyze(hx), section, index).cells]


def _contains(hx, section, index, want):
    """낸 값이 모두 그 표 칸 글자로 들어 있는지(여러 번 낸 같은 값은 그만큼)."""
    have = Counter(_texts(hx, section, index))
    return all(have[v] >= n for v, n in Counter(want).items())


@unittest.skipUnless(DIR, "GT_BENCH_DIR 없음")
@override_settings(ROOT_URLCONF="config.urls", REDIS_URL="")
class Bench(TestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, True)
        o = override_settings(MEDIA_ROOT=media, HWP_SPACE_QUOTA=1 << 40, HWP_TOTAL_QUOTA=1 << 40)
        o.enable()
        self.addCleanup(o.disable)

    def space(self, n):
        school = School.objects.create(neis_atpt="B10", neis_code=f"8{n:06d}", name=f"벤치초{n}")
        sp = Space.objects.create(school=school, grade=4, name="4학년")
        u = get_user_model().objects.create(username=f"google:bench{n}")
        Member.objects.create(space=sp, name="교사1", cls="4-1", user=u)
        for k in range(2, CLASSES + 1):
            Member.objects.create(space=sp, name=f"교사{k}", cls=f"4-{k}")
        c = Client()
        c.force_login(u)
        return sp, c

    def one(self, n, name, rnd):
        """(단계, 결과 dict). 단계: skip:<이유> | fail:<이유> | ok"""
        data = open(os.path.join(DIR, name), "rb").read()
        sp, cl = self.space(n)
        base = f"/s/{sp.token}"
        f = SimpleUploadedFile("양식." + name.rsplit(".", 1)[-1].lower(), data)
        if cl.post(f"{base}/hwp/upload?pick=1", {"file": f}).status_code != 200:
            return "skip:upload", {}
        sf = SourceFile.objects.get(space=sp)
        if sf.status != SourceFile.Status.DONE:
            return "skip:convert", {}
        top = [t for t in sf.report.get("tables", []) if t.get("rank") == 0]
        if not top:
            return "skip:no_candidate", {}
        cl.post(f"{base}/hwp/{sf.key}/pick", {"sec": top[0]["sec"], "index": top[0]["index"]})
        form = cl.get(f"{base}/c/new/hwp/{sf.key}").context["hwpf"]
        if form["error"]:
            return "skip:e6", {}
        post = {"src": "hwp", "title": "벤치", "hwp": sf.key, "hwp_sent": "1", "hwp_table": form["table"],
                "hwp_items": str(len(form["items"])), "outside": "on"}
        for it in form["items"]:
            post.update({f"item_{it['i']}_role": it["role"], f"item_{it['i']}_name": it["name"],
                         f"item_{it['i']}_kind": it["kind"]})
        for x in form["rows"]:
            if x["on"]:
                post[f"row_{x['r']}"] = "on"
        if cl.post(f"{base}/c/new", post).status_code != 302:
            return "skip:e6_create", {}
        sf.refresh_from_db()
        c = sf.collection
        fields = list(c.fields.all())
        # 받는 사람: 링크로 들어와 자기 줄을 낸다(로그인 없음). 명부 밖 한 명도.
        recv = Client()
        recv.post(f"/c/{c.link_token}/outside", {"name": "명부밖교사"})
        sent = {}
        for row in c.rows.all():
            vals = {f"f{f.pos}": _value(f, rnd) for f in fields}
            r = recv.post(f"/c/{c.link_token}/u/{row.key}", {**vals, "by": row.who})
            if r.status_code != 200:
                return "fail:submit", {}
            sent[row.key] = vals
        t0 = time.monotonic()
        cl.post(f"{base}/c/{c.key}/hwp")
        ms = int((time.monotonic() - t0) * 1000)
        rf = ResultFile.objects.get(collection=c)
        info = {"ms": ms, "kind": sf.kind, "mode": sf.cellmap["mode"], "added": rf.added, "copies": rf.copies,
                "cells": sum(len(v) for v in sent.values())}
        if rf.status != ResultFile.Status.DONE:
            return f"fail:{rf.error}", {**info, "problems": rf.problems[:3]}
        # 따로 검증: 칸 매핑에 자리가 있는 낸 값(단위 붙임)이 결과 표에 다 있고, 자리 없는 값 수가 dropped 와 같은지
        cm = sf.cellmap
        lines = {ln["row"]: ln for ln in cm["lines"]}
        col_unit = {w: u for w, _c, u in cm["cols"]}
        template = next((ln["cells"] for ln in cm["lines"] if ln["cells"]), [])
        want, extra, dropped, extra_rows = {}, [], 0, 0
        for k, vals in sent.items():
            ln = lines.get(k)
            is_extra = cm["mode"] == "rows" and (ln is None or ln["r"] is None)
            if is_extra:
                units = col_unit
                extra_rows += any(vals.values())
            else:
                units = {w: x[-1] for w, *x in (ln["cells"] if ln else template)}
            spots = set(units)
            for p, v in vals.items():
                pos = int(p[1:])
                if not v:
                    continue
                if pos not in spots:
                    dropped += 1
                    continue
                u = units[pos]
                v = v + u if u and not v.endswith(u) else v
                (extra if is_extra else want.setdefault(k, [])).append(v)
        # 더한 줄: 다 넣었거나(병합으로 못 넣은 값은 dropped 에), 줄을 못 더했으면 left_out = 낸 더할 줄 수
        if rf.left_out:
            if rf.left_out != extra_rows or rf.dropped != dropped:
                return "fail:left_out_count", info
            extra = []
        elif rf.dropped < dropped:
            return "fail:dropped_count", {**info, "dropped": [dropped, rf.dropped]}
        lost = rf.dropped - dropped
        info.update(dropped=rf.dropped, left_out=rf.left_out)
        sec, idx = cm["section"], cm["index"]
        outs = []
        for which in ("hwpx", "file"):
            if not getattr(rf, which):
                continue
            body = b"".join(cl.get(f"{base}/hwp/out/{rf.key}/{which}").streaming_content)
            if rf.bundle:   # 한 부씩: 낸 줄 순서(명부 순서 → 명부 밖) = zip 순서
                keys = [k for k in sent if any(sent[k].values())]
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    outs += [(which, want.get(k, []), z.read(x)) for k, x in zip(keys, z.namelist())]
            else:
                outs.append((which, [v for vs in want.values() for v in vs], body))
        for which, expect, body in outs:
            hx = hwp.to_hwpx(body)[0] if which == "file" and rf.fmt == "hwp" else body
            if hwp.zip_ok(hx):
                return "fail:zip", info
            if not _contains(hx, sec, idx, expect):
                return f"fail:values_{which}", info
            if extra and not rf.bundle:   # 더한 줄 값: 표에 남은 글자 중 찾은 수 + 병합으로 못 넣은 수 = 더할 값 수
                have = Counter(_texts(hx, sec, idx)) - Counter(expect)
                found = sum(min(have[v], n) for v, n in Counter(extra).items())
                if found + lost < len(extra):
                    miss = [v for v, n in (Counter(extra) - have).items()]
                    return f"fail:extra_{which}", {**info, "extra": [len(extra), found, lost], "miss": miss[:5]}
        if rf.bundle and len(outs) != rf.copies * (2 if rf.file else 1):
            return "fail:copies", info
        return "ok", info

    def test_bench(self):
        names = [x for x in open(os.environ["GT_BENCH_ORDER"], encoding="utf-8").read().split("\n") if x.strip()]
        want = int(os.environ.get("GT_BENCH_N", "50"))
        rnd = random.Random(int(os.environ.get("GT_BENCH_SEED", "1030")))
        out, stages = [], Counter()
        for n, name in enumerate(names):
            if sum(1 for s, _ in out if not s.startswith("skip")) >= want:
                break
            try:
                if os.environ.get("GT_BENCH_DEBUG"):   # 실패 사유(주소·라이브러리 사유 문구, 값 없음)를 찍는다
                    from unittest import mock

                    from . import tasks
                    real = tasks._addr

                    def show(problems):
                        print("   why", [(a[2:4], why) for a, why in problems[:4]], flush=True)
                        return real(problems)
                    with mock.patch.object(tasks, "_addr", show):
                        stage, info = self.one(n, name, rnd)
                else:
                    stage, info = self.one(n, name, rnd)
            except Exception as e:  # 벤치는 멈추지 않고 기록
                stage, info = f"fail:crash_{type(e).__name__}", {}
            stages[stage.split(":")[0] if stage == "ok" else stage] += 1
            out.append((stage, info))
            print(json.dumps({"no": name[:3], "stage": stage, **info}, ensure_ascii=False), flush=True)
        done = [i for s, i in out if not s.startswith("skip")]
        ok = [i for s, i in out if s == "ok"]
        ms = sorted(i["ms"] for i in done if "ms" in i)
        print(f"== F1 벤치: 채우기까지 간 표본 {len(done)}개, 검증 통과 {len(ok)}개, 단계별 {dict(stages)}")
        if ms:
            print(f"   채우기 작업 ms 중앙값 {ms[len(ms) // 2]} / 최대 {ms[-1]}, "
                  f"줄 더한 표본 {sum(1 for i in ok if i['added'])}, 신청서형 {sum(1 for i in ok if i['mode'] == 'cells')}, "
                  f"hwp {sum(1 for i in ok if i['kind'] == 'hwp')}")
