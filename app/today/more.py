"""P2 일정 본문·첨부·댓글. 일정 대화상자 오른쪽 칸(_ev_more.html)을 열 때 받아 오고, 쓰면 그 칸만 htmx로 바꾼다.

- 읽기·댓글 쓰기: 그 일정을 볼 수 있는 사람(reachable_q). 안 보이는 일정·다른 공간은 첨부 내려받기까지 모두 404.
- 본문 쓰기·첨부 올리기·지우기: 일정을 고칠 수 있는 사람(writable_by). 댓글 지우기: 쓴 사람·관리자.
- 첨부 검사·저장 한도·요청 크기 안내는 공지 첨부 것을 그대로 쓴다(notices.views). 올리는 주소는 앞단 큰 본문 라우터에 든다(config.bodylimit).
- 플랜이 끝난 학교도 일정 쓰기와 같게 막지 않는다(리드 결정: 일정은 기존 회원이 계속 쓴다).
- 본문 저장은 '바뀐 것'이 아니다(updated_at을 건드리지 않는다).
"""
import logging

from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from collect.models import Notification
from config.ratelimit import TOO_MANY, allow
from notices.views import ACCEPT, KINDS_SHORT, MAX_FILE, MAX_FILES, MB, TYPES, _body_guard, _clean, _quota_error, _read_files

from .models import ALL, BODY_MAX, COMMENT_MAX, ME, Event, EventComment, EventFile, reachable_q

log = logging.getLogger(__name__)
UPLOAD_PER_HOUR = 30  # 공지 첨부와 같다
COMMENT_PER_HOUR = 60
TOO_MANY_FILES = f"첨부는 일정 하나에 {MAX_FILES}개까지예요."
NO_WRITE = "본문과 첨부는 쓴 선생님과 관리자만 고칠 수 있어요."


def _event(request, key):
    return get_object_or_404(Event.objects.in_space(request.space).filter(reachable_q(request.member)), key=key)


def _part(request, e, status=200, **extra):
    me = request.member
    comments = [{"c": c, "can_del": me.is_manager or (c.by_id is not None and c.by_id == me.pk)}
                for c in e.comments.order_by("at", "pk")]
    ctx = {"space": request.space, "e": e, "can_write": e.writable_by(me), "files": e.files.order_by("at", "pk"),
           "comments": comments, "body_max": BODY_MAX, "comment_max": COMMENT_MAX, "max_files": MAX_FILES,
           "max_mb": MAX_FILE // MB, "max_total_mb": MAX_FILES * MAX_FILE // MB, "accept": ACCEPT, "kinds_text": KINDS_SHORT,
           "this_year": str(timezone.localdate().year), **extra}
    return render(request, "today/_ev_more.html", ctx, status=status)


@never_cache
@require_GET
def more(request, key):
    return _part(request, _event(request, key))


@never_cache
@require_POST
def body(request, key):
    e = _event(request, key)
    if not e.writable_by(request.member):
        return HttpResponseForbidden(NO_WRITE)
    text = _clean(request.POST, "body")
    if len(text) > BODY_MAX:
        return _part(request, e, 400, body_error=f"본문은 {BODY_MAX:,}자까지예요.", body_text=text)
    if text != e.body:
        e.body = text
        e.save(update_fields=["body"])
    return _part(request, e, toast="본문을 저장했어요.")


@never_cache
@require_POST
@_body_guard
def files(request, key):
    """첨부 올리기. 너무 크거나 깨진 요청은 _body_guard가 본문을 읽지 않고 안내만 남긴다."""
    e = _event(request, key)
    if not e.writable_by(request.member):
        return HttpResponseForbidden(NO_WRITE)
    if request.upload_error:
        return _part(request, e, 400, file_error=request.upload_error.replace("방금 쓴 내용은", "아무것도"))
    uploads = request.FILES.getlist("attach")
    if not uploads:
        return _part(request, e, 400, file_error="올릴 파일을 골라 주세요.")
    if e.files.count() + len(uploads) > MAX_FILES:  # 빠른 거절. 저장 직전에 잠그고 다시 센다
        return _part(request, e, 400, file_error=TOO_MANY_FILES)
    if not allow("event-up", request.member.key, UPLOAD_PER_HOUR, 3600):
        return _part(request, e, 429, file_error=TOO_MANY)
    got, error = _read_files(uploads)
    error = error or _quota_error(request.space, got)
    if error:
        return _part(request, e, 400, file_error=error)
    with transaction.atomic():  # 되돌려지면 디스크에 남은 파일은 purge의 고아 정리가 줍는다
        Event.objects.select_for_update().filter(pk=e.pk).first()  # 동시 업로드가 5개를 넘지 않게
        if e.files.count() + len(got) > MAX_FILES:
            return _part(request, e, 400, file_error=TOO_MANY_FILES)
        for f in got:
            row = EventFile(event=e, name=f["name"], kind=f["kind"], stored=f["size"])
            row.file.save(f["kind"], f["file"])
    return _part(request, e, toast=f"첨부 {len(got)}개를 올렸어요.")


@never_cache
@require_POST
def file_delete(request, key, file_key):
    e = _event(request, key)
    f = get_object_or_404(e.files, key=file_key)
    if not e.writable_by(request.member):
        return HttpResponseForbidden(NO_WRITE)
    f.delete()  # 디스크 파일은 커밋 뒤 hwpfiles.retention
    return _part(request, e, toast="첨부를 지웠어요.")


@never_cache
@require_GET
def download(request, key, file_key):
    """내려받기: 일정을 볼 수 있는 회원만, 첨부로만, 캐시 금지(공지 첨부와 같다)."""
    f = get_object_or_404(_event(request, key).files, key=file_key)
    try:
        fh = f.file.open("rb")
    except FileNotFoundError:
        log.warning("event file missing file=%s", f.pk)
        raise Http404
    resp = FileResponse(fh, as_attachment=True, filename=f.name, content_type=TYPES[f.kind])
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


def _bell_link(space, e):
    """일정 화면의 그 주·그 탭으로 가서 대화상자를 연다(schedule.js가 #ev-<key>를 연다)."""
    tab = ("school" if e.scope == ALL else "me" if e.scope == ME else "group") if space.is_school else ""
    return f"/s/{space.token}/schedule?w={e.date:%Y-%m-%d}" + (f"&tab={tab}" if tab else "") + f"#ev-{e.key}"


@never_cache
@require_POST
def comment(request, key):
    e = _event(request, key)
    me = request.member
    text = _clean(request.POST, "body")
    if not text:
        return _part(request, e, 400, comment_error="댓글을 써 주세요.")
    if len(text) > COMMENT_MAX:
        return _part(request, e, 400, comment_error=f"댓글은 {COMMENT_MAX:,}자까지예요.", comment_text=text)
    if not allow("event-cmt", me.key, COMMENT_PER_HOUR, 3600):
        return _part(request, e, 429, comment_error=TOO_MANY, comment_text=text)
    with transaction.atomic():
        EventComment.objects.create(event=e, by=me, by_name=me.name, body=text)
        if e.by_id and e.by_id != me.pk:
            Notification.objects.create(space=request.space, member_id=e.by_id, kind=Notification.Kind.EV_COMMENT,
                                        day=timezone.localdate(), text=f"「{e.title}」 일정에 {me.name} 선생님이 댓글을 남겼어요."[:300],
                                        link=_bell_link(request.space, e), pushed=True)
    return _part(request, e, toast="댓글을 올렸어요.")


@never_cache
@require_POST
def comment_delete(request, key, comment_key):
    e = _event(request, key)
    c = get_object_or_404(e.comments, key=comment_key)
    me = request.member
    if not (me.is_manager or (c.by_id is not None and c.by_id == me.pk)):
        return HttpResponseForbidden("댓글은 쓴 선생님과 관리자만 지울 수 있어요.")
    c.delete()
    return _part(request, e, toast="댓글을 지웠어요.")
