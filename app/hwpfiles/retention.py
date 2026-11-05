"""E3 보관 기간 자동 삭제(개인정보처리방침 3항: 취합 제출 값·올린 파일·결과 파일은 요청자가 지우거나
마감 후 1년이 지나면 파기, 백업은 7일 뒤 함께 지워짐). 관리 명령 purge 가 하루 한 번 부른다.

- 지난 취합: 마감·잠근 때·만든 때·마지막 제출(Cell.at)·마지막 수정(updated_at, 다시 열기 포함) 중 가장 늦은 때부터
  1년(마감 없는 취합은 활동 기준만). 활동과 상관없이 만든 뒤 3년이면 지운다(마감을 먼 앞날로 둔 경우의 상한).
  취합 자체를 지운다(칸·줄·제출 값·알림·결과 파일이 CASCADE로 함께). 제목에도 이름이 들어갈 수 있어서다.
  그 취합으로 만든 올린 파일(SourceFile, SET_NULL)도 같이 지운다.
- 지난 복무(J1): 그날로부터 7일 지난 spaces.Attendance.
- 지난 수리 요청(K1): 완료 90일 뒤, 끝나지 않은 요청도 마지막 변경 1년 뒤 행·사진(사진에 교실 모습이 찍힌다). 플랜이 끝나 처리할 수 없게 된 요청도 지워진다.
- 업체 계정(K2): 해제 30일 뒤, 또는 해제하지 않았어도 학교 플랜이 끝나고 30일 뒤(School.paid_until) 행. 접속 기록은 30일.
- 지난 식판 사진(M1): 급식 날짜로부터 30일 지난 행·사진. 끝난 폰 QR 토큰(M3).
- 지난 협력업체 후기(N2): 쓴 지 3년 지난 후기와 3년 지난 조치 이력.
- 지난 공지(O1): 공지는 있는 동안 보관하고 쓴 사람·관리자가 지우면 함께 지운다. 플랜이 끝나면 지울 수 없으니(읽기 전용) 끝난 날부터 1년 뒤 공지·판·첨부·봄 기록을 지운다.
- 신청 정보(O1): spaces.PlanRequest. 처리한 날(대기 중이면 접수한 날)부터 1년.
- 지난 알림(O2): 읽은 것은 90일, 안 읽은 것은 180일 지나면 지운다(만든 때 기준). 이름이 든 입장 알림은 그 회원이 나갈 때 먼저 지워진다(Notification.about).
- 커뮤니티 신고(Q10): 신고한 지 1년. 글·댓글은 쓴 사람이 지우기 전까지 두되, 운영자가 내린 것은 내린 뒤 90일이면 지운다.
- 나간 회원의 '나' 일정(O1): 쓴 사람이 없어져 아무도 볼 수 없는 today.Event(scope=me). 나간 사람의 권한 변경 기록은 그대로 1년 규칙.
- 안 쓴 올린 파일: 취합에 쓰이지 않은 채 24시간(다른 파일을 골라 버린 것, 만들다 그만둔 것).
- 멈춘 작업: 대기·변환 중·만드는 중으로 RQ_JOB_TIMEOUT×2(600초)를 넘긴 것 → 실패 + 파일 정리(화면을 안 열어도).
- 행이 지워지면 디스크 파일도 지운다(post_delete → 커밋 뒤). SourceFile·ResultFile·library.FormDoc·notices.NoticeFile·repairs.Repair·meals.MealPhoto.
  공지 첨부는 따로 기간을 두지 않고 공지와 함께 지운다(쓴 사람·관리자가 지울 때, 플랜이 끝나고 1년 뒤).
- 고아 파일: hwp/·forms/·notice/·repair/·meal/ 아래 어느 행도 가리키지 않는 파일(1시간 넘은 것만 — 저장 중인 파일 보호).
- 저장 크기(stored): 디스크에 실제로 있는 파일 크기 합과 다르면 고친다(공간·전체 상한 계산이 맞게).
출력·로그에는 건수만 남긴다(제목·이름·값·파일 이름 없음).
"""
import logging
from datetime import timedelta

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import default_storage
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.utils import timezone

from collect.models import Collection, Notification
from community.models import Comment, Post, Report, remove
from meals.models import MealPhoto, PhotoLink
from notices.models import Notice
from partners.models import Review, ReviewAction, Suggestion
from repairs.models import Repair, VendorAccess, VendorAccount
from spaces.models import Attendance, MemberLog, PlanRequest
from today.models import ME, Event

from .models import ResultFile, SourceFile

log = logging.getLogger(__name__)

RETENTION = timedelta(days=365)
MAX_AGE = timedelta(days=3 * 365)
UNUSED_SOURCE = timedelta(hours=24)
ATTENDANCE_DAYS = timedelta(days=7)
REPAIR_DONE = timedelta(days=90)
REPAIR_MAX = timedelta(days=365)
VENDOR_DAYS = timedelta(days=30)
MEAL_PHOTO_DAYS = timedelta(days=30)
MEMBER_LOG_AGE = timedelta(days=365)
SUGGESTION_AGE = timedelta(days=365)
COMMUNITY_REPORT_AGE = timedelta(days=365)
COMMUNITY_HIDDEN_AGE = timedelta(days=90)
NOTICE_AFTER_PLAN = timedelta(days=365)
PLAN_REQUEST_AGE = timedelta(days=365)
READ_BELL_AGE = timedelta(days=90)
UNREAD_BELL_AGE = timedelta(days=180)
ORPHAN_GRACE = timedelta(hours=1)
FILE_FIELDS = {"hwpfiles.SourceFile": ("original", "hwpx"), "hwpfiles.ResultFile": ("file", "hwpx"),
               "library.FormDoc": ("file",), "notices.NoticeFile": ("file",), "repairs.Repair": ("photo",),
               "meals.MealPhoto": ("photo",)}
MEDIA_DIRS = ("hwp", "forms", "notice", "repair", "meal")


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
    """마감·잠근 때·만든 때·마지막 제출·마지막 수정이 모두 1년 전보다 이전인 취합, 또는 만든 지 3년 넘은 취합."""
    cut = now - RETENTION
    Q = models.Q
    quiet = (Q(created_at__lt=cut, updated_at__lt=cut)
             & (Q(due__isnull=True) | Q(due__lt=cut))
             & (Q(closed_at__isnull=True) | Q(closed_at__lt=cut))
             & ~Q(rows__cells__at__gte=cut))
    ids = Collection.objects.filter(quiet | Q(created_at__lt=now - MAX_AGE)).values("pk")
    return Collection.objects.filter(pk__in=ids)


def expired_repairs(now):
    """완료 90일 지난 수리 요청, 또는 마지막 변경이 1년 지난 요청."""
    Q = models.Q
    return Repair.objects.filter(Q(status=Repair.Status.DONE, updated_at__lt=now - REPAIR_DONE) | Q(updated_at__lt=now - REPAIR_MAX))


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
    from notices.models import NoticeFile
    for model in (FormDoc, NoticeFile):
        names.update(n for n in model.objects.values_list("file", flat=True) if n)
    for model in (Repair, MealPhoto):
        names.update(n for n in model.objects.values_list("photo", flat=True) if n)
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
    from notices.models import NoticeFile
    for nf in NoticeFile.objects.all():
        n = _disk_size((nf.file,))
        if n != nf.stored:
            out.append((nf, n))
    for model in (Repair, MealPhoto):
        for r in model.objects.exclude(photo=""):
            n = _disk_size((r.photo,))
            if n != r.stored:
                out.append((r, n))
    return out


def purge(now, dry_run=False):
    """한 번 돌린 결과 건수 dict. dry_run 이면 세기만 한다."""
    n = {}
    # 지난 복무(7일): 민감정보라 맨 앞에서, 뒤 단계의 예외와 상관없이 지운다
    done = Attendance.objects.filter(date__lt=timezone.localdate(now) - ATTENDANCE_DAYS)
    n["attendance"] = done.count()
    if not dry_run:
        done.delete()
    old_repairs = expired_repairs(now)
    n["repairs"] = old_repairs.count()
    if not dry_run:
        with transaction.atomic():
            old_repairs.delete()
    old_meals = MealPhoto.objects.filter(date__lt=timezone.localdate(now) - MEAL_PHOTO_DAYS)
    n["meal_photos"] = old_meals.count()
    if not dry_run:
        with transaction.atomic():
            old_meals.delete()
    old_links = PhotoLink.objects.filter(expires_at__lte=now)
    n["photo_links"] = old_links.count()
    if not dry_run:
        old_links.delete()
    # 업체 접속 기록·해제된 업체 계정(담당자 이름): 30일. 해제하지 않아도 플랜이 끝나고 30일이면(재개하면 paid_until이 밀려 빠진다)
    cut = now - VENDOR_DAYS
    old_access = VendorAccess.objects.filter(at__lt=cut)
    gone = VendorAccount.objects.filter(models.Q(revoked_at__lt=cut) | models.Q(space__school__paid_until__lt=timezone.localdate(now) - VENDOR_DAYS))
    n["vendor_access"], n["vendor_accounts"] = old_access.count(), gone.count()
    if not dry_run:
        old_access.delete()
        gone.delete()
    old_logs = MemberLog.objects.filter(at__lt=now - MEMBER_LOG_AGE)
    n["member_logs"] = old_logs.count()
    if not dry_run:
        old_logs.delete()
    # 업체 추천(N1): 처리한 것과 1년 지난 것
    old_suggestions = Suggestion.objects.exclude(status=Suggestion.Status.PENDING) | Suggestion.objects.filter(created_at__lt=now - SUGGESTION_AGE)
    n["suggestions"] = old_suggestions.count()
    if not dry_run:
        old_suggestions.delete()
    # 후기(N2): 3년 지난 후기와 조치 이력
    old_reviews = Review.objects.filter(created_at__lt=now - MAX_AGE)
    old_actions = ReviewAction.objects.filter(created_at__lt=now - MAX_AGE)
    n["reviews"] = old_reviews.count()
    if not dry_run:
        old_reviews.delete()
        old_actions.delete()
    # 커뮤니티 신고(Q10)
    old_reports = Report.objects.filter(created_at__lt=now - COMMUNITY_REPORT_AGE)
    n["community_reports"] = old_reports.count()
    if not dry_run:
        old_reports.delete()
    # 운영자가 내린 글·댓글(90일)
    hidden_posts, hidden_comments = Post.objects.filter(hidden_at__lt=now - COMMUNITY_HIDDEN_AGE), Comment.objects.filter(hidden_at__lt=now - COMMUNITY_HIDDEN_AGE)
    n["community_hidden"] = hidden_posts.count() + hidden_comments.count()
    if not dry_run:
        remove(hidden_posts, hidden_comments)
    # 플랜이 끝나고 1년 지난 공지(O1). 첨부 파일은 post_delete가 커밋 뒤에 지운다
    old_notices = Notice.objects.filter(space__school__paid_until__lt=timezone.localdate(now) - NOTICE_AFTER_PLAN)
    n["notices"] = old_notices.count()
    if not dry_run:
        with transaction.atomic():
            old_notices.delete()
    # 신청 정보(O1): 처리한 날, 대기 중이면 접수한 날부터 1년
    cut = now - PLAN_REQUEST_AGE
    old_requests = PlanRequest.objects.filter(models.Q(done_at__lt=cut) | models.Q(status=PlanRequest.Status.PENDING, created_at__lt=cut))
    n["plan_requests"] = old_requests.count()
    if not dry_run:
        old_requests.delete()
    # 지난 알림(O2)
    old_bells = Notification.objects.filter(models.Q(read=True, created_at__lt=now - READ_BELL_AGE) | models.Q(created_at__lt=now - UNREAD_BELL_AGE))
    n["notifications"] = old_bells.count()
    if not dry_run:
        old_bells.delete()
    # 나간 회원의 '나' 일정(O1): 쓴 사람(by)이 비면 아무도 못 본다
    orphan_events = Event.objects.filter(scope=ME, by__isnull=True)
    n["my_events"] = orphan_events.count()
    if not dry_run:
        orphan_events.delete()
    # 1) 멈춘 작업 → 실패 + 파일 정리(뒤의 삭제 대상이어도 먼저 상태를 맞춘다)
    stuck = list(stuck_sources(now)) + list(stuck_results(now))
    n["stuck"] = len(stuck)
    if not dry_run:
        for x in stuck:
            x.discard("Timeout")
    # 2) 지난 취합과 그 취합으로 만든 올린 파일
    old = expired_collections(now)
    n["collections"] = old.count()
    n["sources"] = SourceFile.objects.filter(collection__in=old).count()
    if not dry_run:
        with transaction.atomic():
            ids = list(old.values_list("pk", flat=True))
            SourceFile.objects.filter(collection_id__in=ids).delete()  # 결과 파일 CASCADE
            Collection.objects.filter(pk__in=ids).delete()
    # 3) 취합에 안 쓴 올린 파일(24시간)
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
