"""E2 한글 양식 올리기·변환 상태(htmx 폴링). 화면 연결(한글 양식 탭)은 E4/E5.

- 올리기: POST, 회원만(SpaceMiddleware), CSRF. 웹 요청 안에서는 크기와 가벼운 구조 검사(hwp.sniff, 압축 안 풂)만.
  위장(docx·doc 등)·30MB 초과·경로 조작 zip은 400, 회원당 진행 중 변환 상한을 넘으면 429, 공간 저장 상한 초과 400.
- 무거운 검사·변환은 hwp·hwpx 모두 큐 'hwp'의 worker가 한다(폭탄 파일로 앱 컨테이너가 죽지 않게).
- 알림(G2)이 아직 없어 변환 보고는 상태 부분 화면에 문구로 보인다.
"""
import re

from django.conf import settings
from django.core.files.base import ContentFile
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

import hwp
from config import queue
from spaces.models import get_in_space_or_404

from .models import SourceFile
from .tasks import convert

MB = 1024 * 1024
REJECT = {
    "TooLarge": f"{settings.HWP_MAX_UPLOAD // MB}MB 이하 한글 파일만 올릴 수 있어요.",
    "UnsupportedFormat": "한글 파일(.hwp, .hwpx)만 올릴 수 있어요. 이름만 바꾼 파일은 열지 않아요.",
    "UnsafeFile": "파일 구조가 안전하지 않아 열지 않았어요. 한글에서 다시 저장해 올려 주세요.",
    "Empty": "올릴 한글 파일을 골라 주세요.",
    "Quota": "동학년 저장 공간이 가득 찼어요. 보관 기간이 지난 파일이 지워진 뒤 다시 올려 주세요.",
    "TotalQuota": "고티처 서비스 저장 공간이 가득 찼어요. 잠시 뒤 다시 올려 주세요. 계속되면 운영자에게 알려 주세요.",
}
BUSY = "앞서 올린 한글 파일을 읽는 중이에요. 끝난 뒤 올려 주세요."


def _reject(request, why, status=400):
    return render(request, "hwpfiles/_reject.html", {"msg": REJECT.get(why, why)}, status=status)


def _clean_name(raw):
    name = re.sub(r"[\x00-\x1f\x7f]", "", (raw or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[-200:]


@require_POST
def upload(request):
    # 본문을 읽기 전에 선언 크기로 먼저 거른다(멀티파트 머리 몫 1MB 여유).
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > settings.HWP_MAX_UPLOAD + MB:
        return _reject(request, "TooLarge")
    f = request.FILES.get("file")
    if f is None or not f.size:
        return _reject(request, "Empty")
    if f.size > settings.HWP_MAX_UPLOAD:
        return _reject(request, "TooLarge")
    busy = SourceFile.objects.in_space(request.space).filter(
        created_by=request.member, status__in=[SourceFile.Status.PENDING, SourceFile.Status.CONVERTING])
    if busy.count() >= settings.HWP_IN_FLIGHT:
        return _reject(request, BUSY, status=429)
    data = f.read()
    try:
        kind = hwp.sniff(data)
    except (hwp.TooLarge, hwp.UnsupportedFormat, hwp.UnsafeFile) as e:
        return _reject(request, type(e).__name__)
    except hwp.HwpError:
        return _reject(request, "UnsafeFile")
    if SourceFile.used_bytes(request.space) + len(data) > settings.HWP_SPACE_QUOTA:
        return _reject(request, "Quota")
    if SourceFile.used_bytes() + len(data) > settings.HWP_TOTAL_QUOTA:
        return _reject(request, "TotalQuota")
    # 행을 먼저 만들고 파일을 쓴다. 쓰다 실패하면 행을 지워 고아(행 없는 파일·파일 없는 행)를 남기지 않는다.
    sf = SourceFile.objects.create(space=request.space, created_by=request.member, name=_clean_name(f.name),
                                   kind=kind, size=len(data), stored=len(data))
    try:
        sf.original.save("original", ContentFile(data))
    except BaseException:
        if sf.original:
            sf.original.delete(save=False)
        sf.delete()
        raise
    # ATOMIC_REQUESTS를 쓰지 않으므로 이미 커밋됐다(worker가 이 줄을 본다).
    queue.enqueue(convert, sf.pk, queue_name=queue.HWP_QUEUE)
    sf.refresh_from_db()  # 큐 없이(로컬·CI) 바로 끝났으면 결과를 보인다
    return render(request, "hwpfiles/_status.html", {"f": sf, "space": request.space})


@never_cache
@require_GET
def status(request, key):
    """변환 상태(htmx 2초 폴링). 끝나면 폴링을 멈춘 부분 화면을 돌려준다."""
    sf = get_in_space_or_404(SourceFile, request, key=key)
    sf.expire_if_stuck()
    return render(request, "hwpfiles/_status.html", {"f": sf, "space": request.space})
