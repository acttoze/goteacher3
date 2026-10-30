"""E2 변환 작업(큐 'hwp'). 업로드 뷰가 config.queue.enqueue(convert, pk, queue_name="hwp") 로 부른다.

hwp·hwpx 모두 큐로 간다. 무거운 검사(zip 전수 읽기·XML DTD 검색, hwp→HWPX 변환)는 웹이 아니라 여기서 한다.
시간과 상관없이 같은 경로다(표본 최대 6.6초, 전체 최대 205초 → 작업당 300초 상한, 넘으면 rq가 360초에 SIGKILL).
실패하면 원본을 바로 지운다. 로그·DB에는 예외 종류 이름만 남긴다(문서 내용 없음).
"""
import io
import logging
import re
import time
import zipfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone

import hwp
from hwp.engine import section_names

from . import merge
from .models import ResultFile, SourceFile

log = logging.getLogger(__name__)
S = SourceFile.Status
MAX_TABLES = 1000


def _fail(sf, error):
    sf.discard(error)
    log.warning("hwp convert failed file=%s error=%s", sf.pk, error)


def convert(pk):
    """원본 → HWPX. 대기 중인 것만 맡는다(같은 작업이 두 번 와도, 실패 처리된 뒤 늦게 와도 한 번만)."""
    if not SourceFile.objects.filter(pk=pk, status=S.PENDING).update(status=S.CONVERTING):
        return
    sf = SourceFile.objects.get(pk=pk)
    try:
        with sf.original.open("rb") as fh:
            data = fh.read()
        hx, kind, report = hwp.to_hwpx(data)  # hwpx는 변환 없이 zip·XML 전수 검사만
        # 표 구조 요약(글자 없음). 미리보기가 rhwp 표와 짝을 맞춰 후보를 강조한다(E4). 상한은 DB 보호용.
        # 구역은 zip 항목 이름 대신 순번(sec)만 둔다. 이름은 hwp.engine.section_names(…)[sec]로 되찾는다.
        report = {**report, "tables": [{k: v for k, v in t.items() if k != "section"}
                                       for t in hwp.tables(hx)[:MAX_TABLES]]}
        if kind == SourceFile.Kind.HWP:
            sf.hwpx.save("conv.hwpx", ContentFile(hx), save=False)
    except hwp.HwpError as e:
        _fail(sf, type(e).__name__)
        return
    except Exception as e:  # 시간 초과(rq JobTimeoutException)·디스크 오류 포함. 상태를 남기고 큐에도 실패로 기록
        _fail(sf, "Timeout" if "Timeout" in type(e).__name__ else type(e).__name__)
        raise RuntimeError(f"hwp convert failed ({type(e).__name__})") from None  # 예외 문구는 버리고 종류만
    sf.report, sf.status, sf.converted_at = report, S.DONE, timezone.now()
    sf.stored = sf.size + (len(hx) if kind == SourceFile.Kind.HWP else 0)
    sf.save(update_fields=["hwpx", "report", "status", "converted_at", "stored"])


# ---------------------------------------------------------------- F1 채우기
R = ResultFile.Status
ZIP_NAME_BAD = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')


class NoValues(hwp.HwpError):
    """신청서형인데 낸 줄이 없음(만들 부수 0)."""


class Quota(hwp.HwpError):
    """저장 상한을 넘김."""


class TooManyCopies(hwp.HwpError):
    """신청서형 부수가 MAX_COPIES 를 넘음."""


MAX_COPIES = 100                     # 신청서형 한 번에 만들 부수(명부 + 명부 밖 상한보다 넉넉히)
MAX_BUNDLE_BYTES = 300 * 1024 * 1024  # 신청서형 zip 둘을 합친 크기(worker 메모리 안에서 끝나게)


def _addr(problems):
    """FillError.problems → [[행, 열]] (값·사유 없이 주소만)."""
    out = []
    for a, _why in problems:
        rc = list(a[2:4]) if len(a) >= 4 else [None, None]
        if rc not in out:
            out.append(rc)
    return out


def _check(before, out, section, index, values):
    """작업 쪽 재검증: zip_ok + 다시 읽어 각 칸 값 대조(hwp.verify). fill 이 이미 하지만 저장 직전에 한 번 더."""
    v = hwp.verify(before, out, section, index, values)
    if hwp.zip_ok(out) or not v["ok"]:
        raise hwp.FillError("결과 재검증 실패", [((section, index) + tuple(k), "value mismatch") for k in v["values_bad"]]
                            or [((section, index, None, None), "verify failed")])


def _one(base, section, index, values, fmt):
    """한 부: 채우기 → 재검증 → (hwp면) HWP 다시 쓰고 되읽기. (올린 형식 바이트 또는 None, hwpx 바이트)."""
    out = hwp.fill(base, section, index, values)
    _check(base, out, section, index, values)
    return (hwp.to_hwp(out, section, index, values) if fmt == "hwp" else None), out


def _ref_row(s, cellmap):
    """더한 줄을 넣을 기준 줄: 칸 틀 입력 열이 모두 빈 칸인 마지막 자료 줄(복제하면 줄마다 칸이 다 있게).
    그런 줄이 없으면 칸 틀 열에 입력 칸이 있는 마지막 자료 줄."""
    cols = {c for _w, c, _u in cellmap.get("cols", [])}
    need = {c for w, c, _u in cellmap.get("cols", []) if isinstance(w, int)}
    total = set(s["total_rows"])
    have = {(i["row"], i["col"]) for i in s["inputs"]}
    rows = {i["row"] for i in s["inputs"] if i["col"] in cols and i["row"] not in total}
    rows |= {ln["r"] for ln in cellmap.get("lines", []) if ln["r"] is not None}
    if not rows:
        raise hwp.FillError("줄을 더할 자리 없음", [((s["section"], s["index"], None, None), "no data row")])
    full = [r for r in rows if all((r, c) in have for c in need)]
    return max(full or rows)


def _merged(rf, base, cm, rows, vals, room=None):
    """줄 모드: 한 파일에 모두. 표 줄이 모자라거나 명부 밖 줄이 있으면 표 끝에 줄을 더한다.
    줄을 더할 수 없는 표(중첩 표 등)면 있는 줄만 채우고 못 넣은 줄 수(left_out)를 안내한다."""
    section, index = cm["section"], cm["index"]
    p = merge.plan(cm, rows, vals)
    values, hx, dropped = dict(p.values), base, p.dropped
    rf.added = rf.left_out = 0
    if p.extra:
        s = hwp.schema(base, section, index)
        ref = _ref_row(s, cm)
        n = len(p.extra)
        try:
            hx = hwp.add_rows(base, section, index, ref, n)
        except hwp.FillError as e:
            log.warning("hwp add rows failed result=%s cells=%s", rf.pk, _addr(e.problems))
            rf.left_out = n
        else:
            s2 = hwp.schema(hx, section, index)
            h = (s2["rows"] - s["rows"]) // n          # 줄 하나의 높이(세로 병합 묶음이면 2 이상)
            end, first = ref + h - 1, ref + h
            rf.added = n
            values = {((r + n * h) if r > end else r, c): v for (r, c), v in values.items()}  # 기준 줄 아래는 밀림
            got, lost = merge.place(merge.extra_values(cm, p.extra, first, vals, h), s2["inputs"])
            values.update(got)
            dropped += lost
            # 칸 틀에 반 열이 없으면 행 머리 열(왼쪽 첫 열)에 받는 곳·이어 센 번호. 그 줄의 빈 입력 칸일 때만.
            if s["label_cols"] >= 1 and not any(w == "unit" or c == 0 for w, c, _u in cm.get("cols", [])):
                ok = {(i["row"], i["col"]) for i in s2["inputs"]}
                ref_head = dict((r, hd) for r, hd in s["row_heads"]).get(ref, "")
                for k, row in enumerate(p.extra):
                    at = (first + k * h, 0)
                    v = merge.head_value(ref_head, k, row)
                    if v and at in ok and at not in values:
                        values[at] = v
    main, out = _one(hx, section, index, values, rf.fmt)
    rf.conflicts, rf.copies, rf.dropped = [list(x) for x in p.conflicts], 1, dropped
    return main, out


def _zip_name(n, row, ext):
    who = ZIP_NAME_BAD.sub("", f"{row.unit}_{row.who}".strip("_"))[:60].strip() or "선생님"
    return f"{n:02d}_{who}.{ext}"


def _bundle(rf, base, cm, rows, vals, room):
    """칸 모드(신청서형): 낸 줄마다 한 부 → zip 두 개(올린 형식, hwpx). 한 부마다 누적 크기를 남은 용량(room)·
    MAX_BUNDLE_BYTES 와 비교해 넘으면 바로 Quota(메모리에 다 쌓기 전에 멈춤)."""
    p = merge.plan(cm, rows, vals)
    if not p.copies:
        raise NoValues("낸 줄 없음")
    if len(p.copies) > MAX_COPIES:
        raise TooManyCopies("부수 상한")
    limit = min(room, MAX_BUNDLE_BYTES)
    zm, zx = io.BytesIO(), io.BytesIO()
    with zipfile.ZipFile(zm, "w", zipfile.ZIP_DEFLATED) as a, zipfile.ZipFile(zx, "w", zipfile.ZIP_STORED) as b:
        for n, (row, values) in enumerate(p.copies, 1):
            main, out = _one(base, cm["section"], cm["index"], values, rf.fmt)
            if main is not None:
                a.writestr(_zip_name(n, row, rf.fmt), main)
            b.writestr(_zip_name(n, row, "hwpx"), out)
            if zm.tell() + zx.tell() > limit:
                raise Quota("저장 상한")
    rf.copies, rf.dropped = len(p.copies), p.dropped
    return (zm.getvalue() if rf.fmt == "hwp" else None), zx.getvalue()


def _values(c):
    """{Row.key: {Field.pos: 값}} — 계산 칸은 Cell 이 없다."""
    from collect.models import Cell
    out = {}
    for key, pos, value in Cell.objects.filter(row__collection=c).values_list("row__key", "field__pos", "value"):
        out.setdefault(key, {})[pos] = value
    return out


def _fail_result(rf, error, problems=()):
    rf.discard(error, problems)
    # 주소(행·열)만 남긴다. 값·이름·파일 이름은 쓰지 않는다.
    log.warning("hwp fill failed result=%s error=%s cells=%s", rf.pk, error, rf.problems)


def fill_result(pk):
    """모으기 제출 값 → 원본 표. 대기 중인 것만 맡는다. 검증을 통과한 결과만 파일로 남긴다."""
    if not ResultFile.objects.filter(pk=pk, status=R.PENDING).update(status=R.FILLING):
        return
    rf = ResultFile.objects.select_related("source", "collection").get(pk=pk)
    sf, c = rf.source, rf.collection
    t0 = time.monotonic()
    try:
        with sf.hwpx_file().open("rb") as fh:
            base = fh.read()
        cm = sf.cellmap
        with zipfile.ZipFile(io.BytesIO(base)) as z:
            if section_names(z.namelist())[cm["sec"]] != cm["section"]:
                raise hwp.FillError("구역 불일치", [((cm["section"], cm["index"], None, None), "section changed")])
        rows = list(c.rows.order_by("pos", "id"))
        vals = _values(c)
        older = ResultFile.objects.filter(collection=c).exclude(pk=rf.pk)
        freed = older.aggregate(n=models.Sum("stored"))["n"] or 0   # 끝나면 지울 앞 결과
        room = min(settings.HWP_SPACE_QUOTA - SourceFile.used_bytes(rf.space),
                   settings.HWP_TOTAL_QUOTA - SourceFile.used_bytes()) + freed
        main, out = (_bundle if cm.get("mode") == "cells" else _merged)(rf, base, cm, rows, vals, room)
        size = len(out) + (len(main) if main is not None else 0)
        if size > room:
            raise Quota("저장 상한")
        if main is not None:
            rf.file.save("file", ContentFile(main), save=False)
        rf.hwpx.save("hwpx", ContentFile(out), save=False)
    except hwp.FillError as e:
        _fail_result(rf, "FillError", _addr(e.problems))
        return
    except hwp.HwpError as e:
        _fail_result(rf, type(e).__name__)
        return
    except Exception as e:  # rq 에 실패로 남기되 예외 문구(문서 글자가 섞일 수 있음)는 버리고 종류만
        name = "Timeout" if "Timeout" in type(e).__name__ else type(e).__name__
        _fail_result(rf, name)
        raise RuntimeError(f"hwp fill failed ({type(e).__name__})") from None
    rf.status, rf.done_at, rf.stored = R.DONE, timezone.now(), size
    rf.save(update_fields=["file", "hwpx", "status", "done_at", "stored", "conflicts", "added", "left_out", "copies", "dropped"])
    for old in older.exclude(status__in=[R.PENDING, R.FILLING]):  # 모으기마다 마지막 결과 하나만
        old.drop_files()
        old.delete()
    log.info("hwp fill done result=%s ms=%d", rf.pk, int((time.monotonic() - t0) * 1000))
