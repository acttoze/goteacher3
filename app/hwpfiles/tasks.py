"""E2 변환 작업(큐 'hwp'). 업로드 뷰가 config.queue.enqueue(convert, pk, queue_name="hwp") 로 부른다.

hwp·hwpx 모두 큐로 간다. 무거운 검사(zip 전수 읽기·XML DTD 검색, hwp→HWPX 변환)는 웹이 아니라 여기서 한다.
시간과 상관없이 같은 경로다(표본 최대 6.6초, 전체 최대 205초 → 작업당 300초 상한, 넘으면 rq가 360초에 SIGKILL).
실패하면 원본을 바로 지운다. 로그·DB에는 예외 종류 이름만 남긴다(문서 내용 없음).
"""
import logging

from django.core.files.base import ContentFile
from django.utils import timezone

import hwp

from .models import SourceFile

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
        raise
    sf.report, sf.status, sf.converted_at = report, S.DONE, timezone.now()
    sf.stored = sf.size + (len(hx) if kind == SourceFile.Kind.HWP else 0)
    sf.save(update_fields=["hwpx", "report", "status", "converted_at", "stored"])
