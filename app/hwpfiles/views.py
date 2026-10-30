"""E2 한글 양식 올리기·변환 상태(htmx 폴링), E4 쪽 미리보기·HWPX 내려주기, E5 표 고르기(만들기 화면 한글 양식 탭).

- 올리기: POST, 회원만(SpaceMiddleware), CSRF. 웹 요청 안에서는 크기와 가벼운 구조 검사(hwp.sniff, 압축 안 풂)만.
  위장(docx·doc 등)·30MB 초과·경로 조작 zip은 400, 회원당 진행 중 변환 상한을 넘으면 429, 공간 저장 상한 초과 400.
- 무거운 검사·변환은 hwp·hwpx 모두 큐 'hwp'의 worker가 한다(폭탄 파일로 앱 컨테이너가 죽지 않게).
- 알림(G2)이 아직 없어 변환 보고는 상태 부분 화면에 문구로 보인다.
- 미리보기(E4): 변환이 끝나면 브라우저가 hwpx 뷰로 파일을 받아 rhwp(WASM, web/static/hwp/)로 쪽별 SVG를 그린다.
  파일은 이 hwpx 뷰로만 나간다(회원·같은 공간·완료된 것만, 캐시 금지). MEDIA_URL 공개 서빙은 하지 않는다.
- 표 고르기(E5): 만들기 화면이 ?pick=1 로 올리면 상태 대신 고르기 부분 화면(_pick.html)이 오고, 그 화면이
  변환이 끝날 때까지 자신을 폴링한다. 끝나면 미리보기 위 표를 눌러 확정(POST pick, CSRF).
  확정은 회원·같은 공간·완료된 파일·report["tables"] 안의 표 위치일 때만 저장한다.
"""
import logging
import re

from django.conf import settings
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

import hwp
from config import queue
from spaces.models import get_in_space_or_404
from spaces.views import _space_ctx

from .models import SourceFile
from .tasks import convert

log = logging.getLogger(__name__)
MB = 1024 * 1024
REJECT = {
    "TooLarge": f"{settings.HWP_MAX_UPLOAD // MB}MB 이하 한글 파일만 올릴 수 있어요.",
    "UnsupportedFormat": "한글 파일(.hwp, .hwpx)만 올릴 수 있어요. 이름만 바꾼 파일은 열지 않아요.",
    "UnsafeFile": "파일 구조가 안전하지 않아 열지 않았어요. 한글에서 다시 저장해 올려 주세요.",
    "Empty": "올릴 한글 파일을 골라 주세요.",
    "Quota": "동학년 저장 공간이 가득 찼어요. 보관 기간이 지난 파일이 지워진 뒤 다시 올려 주세요.",
    "TotalQuota": "고티처 서비스 저장 공간이 가득 찼어요. 잠시 뒤 다시 올려 주세요. 계속되면 운영자에게 알려 주세요.",
}
# 미리보기 화면에만 거는 CSP. WASM 컴파일에 'wasm-unsafe-eval'(크롬 97+)만 더한다. 스크립트 외부 출처는 없다.
# 외부는 gt.css가 @import 하는 Wanted Sans 글꼴(CSS와 woff2가 이 경로 아래)만, style·font에만 연다.
# 쪽 SVG는 blob: <img>로 넣는다(문서에서 나온 SVG를 DOM에 직접 넣지 않음). 인라인 style은 공통 틀(shell)이 쓴다.
FONT_SRC = "https://cdn.jsdelivr.net/gh/wanteddev/wanted-sans@v1.0.3/"
PREVIEW_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; "
               f"style-src 'self' 'unsafe-inline' {FONT_SRC}; img-src 'self' blob: data:; font-src 'self' {FONT_SRC}; "
               "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
HWPX_TYPE = "application/hwp+zip"
TABLE_KEYS = ("sec", "index", "rows", "cols", "nested", "parent", "role", "rank")
MAX_CHIPS = 6  # 고르기 화면 위 후보 단추 수(나머지 후보는 미리보기에서 점선으로)
BUSY = "앞서 올린 한글 파일을 읽는 중이에요. 끝난 뒤 올려 주세요."


def _tables(sf):
    """미리보기용 표 요약(글자 없음). no 는 화면의 '표 N'(문서 전체 순서)."""
    return [{**{k: t.get(k) for k in TABLE_KEYS}, "no": no} for no, t in enumerate(sf.report.get("tables", []), 1)]


def pick_ctx(sf):
    """고르기 부분 화면(_pick.html) 값. 만들기 화면(collect.new)이 다시 그릴 때도 쓴다."""
    tables = _tables(sf)
    cands = sorted((t for t in tables if t["rank"] is not None), key=lambda t: t["rank"])[:MAX_CHIPS]
    return {"f": sf, "tables": tables, "cands": cands, "picked": sf.picked}


def _pick_part(request, sf):
    return render(request, "hwpfiles/_pick.html", {**pick_ctx(sf), "space": request.space})


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
    if request.GET.get("pick"):
        return _pick_part(request, sf)
    return render(request, "hwpfiles/_status.html", {"f": sf, "space": request.space})


@never_cache
@require_GET
def status(request, key):
    """변환 상태(htmx 2초 폴링). 끝나면 폴링을 멈춘 부분 화면을 돌려준다."""
    sf = get_in_space_or_404(SourceFile, request, key=key)
    sf.expire_if_stuck()
    return render(request, "hwpfiles/_status.html", {"f": sf, "space": request.space})


@never_cache
@require_GET
def preview(request, key):
    """쪽 미리보기(최소). 변환 중이면 상태 부분 화면이 폴링하고, 끝나면 web/static/hwp/preview.js가 그린다.
    표 고르기(클릭 확정)는 만들기 화면의 고르기 부분 화면(pick)에서. 여기서는 표 영역 표시까지."""
    sf = get_in_space_or_404(SourceFile, request, key=key)
    sf.expire_if_stuck()
    ctx = _space_ctx(request, f=sf, tables=_tables(sf), page="collect")
    resp = render(request, "hwpfiles/preview.html", ctx)
    resp["Content-Security-Policy"] = PREVIEW_CSP
    return resp


@never_cache
@require_http_methods(["GET", "POST"])
def pick(request, key):
    """GET: 만들기 화면의 고르기 부분 화면(변환 중이면 2초 폴링). POST: 표 확정 {sec, index} → JSON.
    확정은 완료된 파일의 report["tables"] 안에 있는 표만. 오류 문구에는 표 주소도 넣지 않는다."""
    sf = get_in_space_or_404(SourceFile, request, key=key)
    if request.method == "GET":
        sf.expire_if_stuck()
        return _pick_part(request, sf)
    if sf.status != SourceFile.Status.DONE:
        return JsonResponse({"error": "한글 파일을 다 읽은 뒤 표를 골라 주세요."}, status=409)
    try:
        sec, index = int(request.POST["sec"]), int(request.POST["index"])
    except (KeyError, ValueError):
        sec = index = None
    t = sf.table_at(sec, index) if sec is not None else None
    if t is None:
        return JsonResponse({"error": "그 표를 찾지 못했어요. 미리보기에서 표를 다시 눌러 주세요."}, status=400)
    sf.table_sec, sf.table_index = sec, index
    sf.save(update_fields=["table_sec", "table_index"])
    return JsonResponse({"sec": sec, "index": index, "no": t["no"], "rank": t.get("rank")})


@never_cache
@require_GET
def hwpx(request, key):
    """변환한 HWPX(원본이 hwpx면 원본). 회원·같은 공간·완료된 파일만. 미리보기와 E6 내려받기가 쓴다."""
    sf = get_in_space_or_404(SourceFile, request, key=key)
    f = sf.hwpx_file()
    if sf.status != SourceFile.Status.DONE or not f:
        raise Http404
    try:
        fh = f.open("rb")
    except FileNotFoundError:  # 상태는 완료인데 디스크에 없음(볼륨 복구 누락 등). 500 대신 404
        log.warning("hwp file missing file=%s", sf.pk)
        raise Http404
    stem = re.sub(r"\.(hwpx?|HWPX?)$", "", sf.name) or "한글양식"
    resp = FileResponse(fh, as_attachment=True, filename=f"{stem}.hwpx", content_type=HWPX_TYPE)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp
