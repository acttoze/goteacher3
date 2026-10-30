"""E3 보관 기간 자동 삭제(개인정보처리방침 3항: 모으기 제출 값·올린 파일·결과 파일은 요청자가 지우거나
마감 후 1년이 지나면 파기, 백업은 7일 뒤 함께 지워짐). 관리 명령 purge 가 하루 한 번 부른다.

- 지난 모으기: 마감·잠근 때·만든 때·마지막 제출(Cell.at)·마지막 수정(updated_at, 다시 열기 포함) 중 가장 늦은 때부터
  1년(마감 없는 모으기는 활동 기준만). 활동과 상관없이 만든 뒤 3년이면 지운다(마감을 먼 앞날로 둔 경우의 상한).
  모으기 자체를 지운다(칸·줄·제출 값·알림·결과 파일이 CASCADE로 함께). 제목에도 이름이 들어갈 수 있어서다.
  그 모으기로 만든 올린 파일(SourceFile, SET_NULL)도 같이 지운다.
- 안 쓴 올린 파일: 모으기에 쓰이지 않은 채 24시간(다른 파일을 골라 버린 것, 만들다 그만둔 것).
- 멈춘 작업: 대기·변환 중·만드는 중으로 RQ_JOB_TIMEOUT×2(600초)를 넘긴 것 → 실패 + 파일 정리(화면을 안 열어도).
- 행이 지워지면 디스크 파일도 지운다(post_delete → 커밋 뒤). SourceFile·ResultFile·library.FormDoc.
- 고아 파일: hwp/·forms/ 아래 어느 행도 가리키지 않는 파일(1시간 넘은 것만 — 저장 중인 파일 보호).
- 저장 크기(stored): 디스크에 실제로 있는 파일 크기 합과 다르면 고친다(공간·전체 상한 계산이 맞게).
출력·로그에는 건수만 남긴다(제목·이름·값·파일 이름 없음).
"""
import logging
from datetime import timedelta

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import default_storage
from django.db import models, transaction
from django.db.models.signals import post_delete

from collect.models import Collection

from .models import ResultFile, SourceFile

log = logging.getLogger(__name__)

RETENTION = timedelta(days=365)
MAX_AGE = timedelta(days=3 * 365)
UNUSED_SOURCE = timedelta(hours=24)
ORPHAN_GRACE = timedelta(hours=1)
FILE_FIELDS = {"hwpfiles.SourceFile": ("original", "hwpx"), "hwpfiles.ResultFile": ("file", "hwpx"),
               "library.FormDoc": ("file",)}
MEDIA_DIRS = ("hwp", "forms")


# ---------------------------------------------------------------- 행이 지워지면 파일도
def _drop_files(sender, instance, **kwargs):
    files = [getattr(instance, f) for f in FILE_FIELDS[sender._meta.label]]
    pairs = [(f.storage, f.name) for f in files if f]
    if not pairs:
        return

    def run():
        for storage, name in pairs:
            try:
                storage.delete(name)
            except OSError as e:  # 지우지 못한 파일은 다음 purge 의 고아 정리가 줍는다
                log.warning("file delete failed error=%s", type(e).__name__)

    transaction.on_commit(run)  # 되돌린 삭제에서 파일만 사라지지 않게


def connect():
    for label in FILE_FIELDS:
        post_delete.connect(_drop_files, sender=label, dispatch_uid=f"drop_files:{label}")


# ---------------------------------------------------------------- purge
def expired_collections(now):
    """마감·잠근 때·만든 때·마지막 제출·마지막 수정이 모두 1년 전보다 이전인 모으기, 또는 만든 지 3년 넘은 모으기."""
    cut = now - RETENTION
    Q = models.Q
    quiet = (Q(created_at__lt=cut, updated_at__lt=cut)
             & (Q(due__isnull=True) | Q(due__lt=cut))
             & (Q(closed_at__isnull=True) | Q(closed_at__lt=cut))
             & ~Q(rows__cells__at__gte=cut))
    ids = Collection.objects.filter(quiet | Q(created_at__lt=now - MAX_AGE)).values("pk")
    return Collection.objects.filter(pk__in=ids)


def _stuck(model, busy, now):
    from django.conf import settings
    return model.objects.filter(status__in=busy, created_at__lt=now - timedelta(seconds=settings.RQ_JOB_TIMEOUT * 2))


def stuck_sources(now):
    S = SourceFile.Status
    return _stuck(SourceFile, [S.PENDING, S.CONVERTING], now)


def stuck_results(now):
    R = ResultFile.Status
    return _stuck(ResultFile, [R.PENDING, R.FILLING], now)


def unused_sources(now):
    return SourceFile.objects.filter(collection__isnull=True, created_at__lt=now - UNUSED_SOURCE)


def _referenced():
    names = set()
    for model, fields in ((SourceFile, ("original", "hwpx")), (ResultFile, ("file", "hwpx"))):
        for row in model.objects.values_list(*fields):
            names.update(n for n in row if n)
    from library.models import FormDoc
    names.update(n for n in FormDoc.objects.values_list("file", flat=True) if n)
    return names


def _walk(storage, top):
    try:
        dirs, files = storage.listdir(top)
    except FileNotFoundError:
        return
    for f in files:
        yield f"{top}/{f}"
    for d in dirs:
        yield from _walk(storage, f"{top}/{d}")


def orphan_files(now, storage=default_storage):
    """디스크에만 있는 파일 이름(1시간 넘은 것). 행을 만든 뒤 파일을 쓰고 이름을 저장하는 사이를 건드리지 않는다."""
    ref = _referenced()
    out = []
    for top in MEDIA_DIRS:
        for name in _walk(storage, top):
            if name in ref:
                continue
            try:
                old = storage.get_modified_time(name) < now - ORPHAN_GRACE
            except OSError:   # 목록과 확인 사이에 사라짐
                continue
            if old:
                out.append(name)
    return out


def _disk_size(files):
    total = 0
    for f in files:
        if f:
            try:
                total += f.storage.size(f.name)
            except OSError:
                pass
    return total


def stored_mismatches():
    """[(행, 실제 크기)] — 작업이 끝난(대기·진행 중이 아닌) 행만. 진행 중에는 stored 가 예약 값이다."""
    out = []
    S, R = SourceFile.Status, ResultFile.Status
    for sf in SourceFile.objects.exclude(status__in=[S.PENDING, S.CONVERTING]):
        n = _disk_size((sf.original, sf.hwpx))
        if n != sf.stored:
            out.append((sf, n))
    for rf in ResultFile.objects.exclude(status__in=[R.PENDING, R.FILLING]):
        n = _disk_size((rf.file, rf.hwpx))
        if n != rf.stored:
            out.append((rf, n))
    return out


def purge(now, dry_run=False):
    """한 번 돌린 결과 건수 dict. dry_run 이면 세기만 한다."""
    n = {}
    # 1) 멈춘 작업 → 실패 + 파일 정리(뒤의 삭제 대상이어도 먼저 상태를 맞춘다)
    stuck = list(stuck_sources(now)) + list(stuck_results(now))
    n["stuck"] = len(stuck)
    if not dry_run:
        for x in stuck:
            x.discard("Timeout")
    # 2) 지난 모으기와 그 모으기로 만든 올린 파일
    old = expired_collections(now)
    n["collections"] = old.count()
    n["sources"] = SourceFile.objects.filter(collection__in=old).count()
    if not dry_run:
        with transaction.atomic():
            ids = list(old.values_list("pk", flat=True))
            SourceFile.objects.filter(collection_id__in=ids).delete()  # 결과 파일 CASCADE
            Collection.objects.filter(pk__in=ids).delete()
    # 3) 모으기에 안 쓴 올린 파일(24시간)
    unused = unused_sources(now)
    n["sources"] += unused.count()
    if not dry_run:
        with transaction.atomic():
            unused.delete()
    # 4) 고아 파일, 5) 저장 크기 정합
    orphans = orphan_files(now)
    n["orphans"] = len(orphans)
    mism = stored_mismatches()
    n["stored"] = len(mism)
    n["failed"] = 0
    if not dry_run:
        for name in orphans:   # 한 건이 실패해도 나머지와 정합은 이어 간다(실패 건수만)
            try:
                default_storage.delete(name)
            except (OSError, SuspiciousFileOperation):
                n["failed"] += 1
        for row, size in mism:
            type(row).objects.filter(pk=row.pk).update(stored=size)
    return n
