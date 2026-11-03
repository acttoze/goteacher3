"""I1 공지 게시판(prototype board·noticeDetail): 쓰기·목록·상세·판 보기·첨부. 확인·현황·쪽지 링크·🔔은 I2.

- 보기는 NoticeQuerySet.visible_to로만(대상 밖이면 상세·옛 판·첨부 모두 404). 고치기·지우기는 쓴 사람·관리자, 직위 글자는 안 본다.
- 충돌: 폼이 고칠 때의 판(base_v)을 들고 다니다가, 저장 때 최신 판과 다르면 저장하지 않고 쓴 내용과 함께 돌려준다(409).
- 첨부 검사는 hwp.sniff(압축 안 풂, 선언 크기로 폭탄 거부)와 PDF 머리. 서버는 첨부를 풀지 않으므로 웹 요청에서 이것만 본다.
- 요청 크기: CSRF 미들웨어가 뷰보다 먼저 본문을 풀기 때문에, 쓰기·고치기는 _body_guard로 CSRF 검사를 뷰 앞으로 옮겨
  선언 크기를 본문보다 먼저 본다.
- 제목·본문은 자동 이스케이프, 본문 줄바꿈만 linebreaksbr.
"""
import difflib
import logging
import os
from functools import wraps

from django.conf import settings
from django.core.exceptions import TooManyFilesSent
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

import hwp
from config.ratelimit import TOO_MANY, allow
from hwpfiles.models import SourceFile, space_quota
from hwpfiles.views import _clean_name
from spaces.gates import school_required, school_write_required
from spaces.models import Member
from spaces.views import _space_ctx

from .models import AUDIENCE, FILE_LIST, FILES, ITEMS, ORDER, TITLE, Notice, NoticeFile, NoticeVersion, audience_value

log = logging.getLogger(__name__)
MB = 1024 * 1024
PER_PAGE = 20
MAX_TITLE, MAX_ITEM, MAX_BODY = 200, 200, 10000
MAX_FILES, MAX_FILE = 5, 20 * MB
MAX_REQUEST = MAX_FILES * MAX_FILE + MB  # 선언 크기 상한(멀티파트 머리·글 몫 1MB 여유)
UPLOAD_PER_HOUR = 30  # 회원 한 명이 시간당 첨부를 담아 저장하는 횟수(거부된 것도 센다)
TYPES = {"hwp": "application/x-hwp", "hwpx": "application/hwp+zip", "pdf": "application/pdf"}
TOO_BIG = f"첨부는 합쳐서 {MAX_FILES * MAX_FILE // MB}MB까지 올릴 수 있어요. 파일을 줄여 다시 올려 주세요. 방금 쓴 내용은 저장되지 않았어요."
BAD_UPLOAD = "올린 내용을 읽지 못했어요. 파일 이름이 너무 길거나 파일이 너무 많아요. 이름을 줄여 다시 올려 주세요. 방금 쓴 내용은 저장되지 않았어요."
TOO_MANY_FILES = f"첨부는 공지 하나에 {MAX_FILES}개까지예요."
QUOTA = "학교 저장 공간이 가득 찼어요. 보관 기간이 지난 파일이 지워진 뒤 다시 올려 주세요."
TOTAL_QUOTA = "고티처 서비스 저장 공간이 가득 찼어요. 잠시 뒤 다시 올려 주세요. 계속되면 운영자에게 알려 주세요."


def _visible(request, key):
    return get_object_or_404(Notice.objects.in_space(request.space).visible_to(request.member), key=key)


def _can_edit(member, n):
    return member.is_manager or n.by_id == member.pk


def _latest(n):
    return n.versions.order_by("-v").first()


def _changes(prev, cur):
    """바뀐 항목만 [{name, old, new}]."""
    changed = [k for k in ORDER if prev.get(k, "") != cur.get(k, "")]
    if FILES not in changed and prev.get(FILE_LIST) != cur.get(FILE_LIST):  # 같은 이름의 다른 파일로 바꿈
        changed.append(FILES)
    return [{"name": k, "old": prev.get(k, ""), "new": cur.get(k, "")} for k in changed]


def _body_diff(old, new):
    """바뀐 줄만 [{op, text}]. SequenceMatcher 기본 autojunk가 같은 줄이 아주 많은 본문에서도 느려지지 않게 한다."""
    a, b, out = old.splitlines(), new.splitlines(), []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag != "equal":
            out += [{"op": "del", "text": t} for t in a[i1:i2]] + [{"op": "ins", "text": t} for t in b[j1:j2]]
    return out


def _body_guard(view):
    """선언 크기가 넘으면 본문을 읽지 않고, 멀티파트가 깨졌으면 안내 문구만 그리게 request.upload_error를 둔다.
    이때 뷰는 POST를 읽거나 저장하지 않고 폼만 그리므로 CSRF 검사 없이 불러도 된다."""
    protected = csrf_protect(view)

    @csrf_exempt
    @wraps(view)
    def inner(request, *args, **kwargs):
        request.upload_error = ""
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if request.method == "POST" and declared > MAX_REQUEST:
            request.upload_error = TOO_BIG
            return view(request, *args, **kwargs)
        try:
            return protected(request, *args, **kwargs)
        except (MultiPartParserError, TooManyFilesSent):
            request.upload_error = BAD_UPLOAD
            return view(request, *args, **kwargs)
    return inner


# ---------------------------------------------------------------- 폼
def _clean(data, name):
    return data.get(name, "").replace("\x00", "").replace("\r\n", "\n").strip()


def _read_form(data):
    """제출 값 → (폼 값, 오류). 저장할 값도 이 폼 값이다."""
    form = {"title": _clean(data, "title"), "body": _clean(data, "body"),
            "items": [{"name": k, "value": _clean(data, k)} for k in ITEMS],
            "aud": "groups" if data.get("aud") == "groups" else "all",
            "groups": [g for g in Member.Group.values if g in data.getlist("groups")]}
    errors = {}
    if not form["title"]:
        errors["title"] = "제목을 적어 주세요."
    elif len(form["title"]) > MAX_TITLE:
        errors["title"] = f"제목은 {MAX_TITLE}자까지예요."
    if any(len(i["value"]) > MAX_ITEM for i in form["items"]):
        errors["item"] = f"항목은 {MAX_ITEM}자까지예요."
    if len(form["body"]) > MAX_BODY:
        errors["body"] = f"본문은 {MAX_BODY:,}자까지예요."
    if form["aud"] == "groups" and not form["groups"]:
        errors["aud"] = "받을 그룹을 하나 이상 골라 주세요."
    return form, errors


def _judge(data):
    """내용으로 본 형식. 위장·폭탄이면 hwp.HwpError."""
    return "pdf" if data[:5] == b"%PDF-" else hwp.sniff(data)


def _read_files(uploads):
    """올린 파일 → ([{name, kind, data}], 오류). 이름의 확장자와 내용이 같은 형식일 때만 받는다."""
    if len(uploads) > MAX_FILES:
        return [], TOO_MANY_FILES
    out = []
    for f in uploads:
        name = _clean_name(f.name)
        if f.size > MAX_FILE:
            return [], f"「{name}」: 첨부는 {MAX_FILE // MB}MB까지예요."
        if not f.size:
            return [], f"「{name}」: 빈 파일이에요."
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        if ext not in TYPES:
            return [], f"「{name}」: 한글(.hwp, .hwpx)과 PDF만 올릴 수 있어요."
        data = f.read()
        try:
            kind = _judge(data)
        except hwp.UnsafeFile:
            return [], f"「{name}」: 파일 구조가 안전하지 않아 받지 않았어요. 다시 저장해 올려 주세요."
        except hwp.HwpError:
            kind = ""
        if kind != ext:
            return [], f"「{name}」: 파일 이름과 내용이 달라요. 진짜 한글·PDF 파일만 올릴 수 있어요."
        out.append({"name": name, "kind": kind, "data": data})
    return out, ""


def _quota_error(space, files):
    size = sum(len(f["data"]) for f in files)
    if not size:
        return ""
    if SourceFile.used_bytes(space) + size > space_quota(space):
        return QUOTA
    if SourceFile.used_bytes() + size > settings.HWP_TOTAL_QUOTA:
        return TOTAL_QUOTA
    return ""


def _uploads(request):
    """첨부와 빈도 제한. (파일들, 오류, 오류일 때 상태 코드). 빈도는 파일을 읽기 전에 센다."""
    uploads = request.FILES.getlist("attach")
    if not uploads:
        return [], "", 400
    if not allow("notice-up", request.member.key, UPLOAD_PER_HOUR, 3600):
        return [], TOO_MANY, 429
    files, error = _read_files(uploads)
    return files, error or _quota_error(request.space, files), 400


def _add_version(n, form, by):
    """지금 공지 상태로 판 하나. 변경이 없으면 아무것도 하지 않는다. n.audience·첨부 행은 이미 바뀌어 있어야 한다."""
    files = list(n.files.order_by("at", "pk").values("key", "name"))
    fields = {TITLE: form["title"], **{i["name"]: i["value"] for i in form["items"]},
              AUDIENCE: n.audience_text, FILES: ", ".join(f["name"] for f in files), FILE_LIST: files}
    latest = _latest(n)
    if latest is not None and latest.fields == fields and latest.body == form["body"]:
        return None
    return NoticeVersion.objects.create(notice=n, v=latest.v + 1 if latest else 1, fields=fields, body=form["body"], by=by)


def _save_files(n, files):
    """트랜잭션 안에서 부른다. 되돌려지면 디스크에 남은 파일은 다음 purge의 고아 정리가 줍는다."""
    for f in files:
        row = NoticeFile(notice=n, name=f["name"], kind=f["kind"], stored=len(f["data"]))
        row.file.save(f["kind"], ContentFile(f["data"]))


def _form_page(request, form, status=200, **extra):
    picked = set(form["groups"])
    ctx = _space_ctx(request, page="board", form=form, writable=request.space.school.writable, max_files=MAX_FILES,
                     max_mb=MAX_FILE // MB, group_opts=[{"id": g, "label": label, "on": g in picked}
                                                        for g, label in Member.Group.choices], **extra)
    return render(request, "notices/form.html", ctx, status=status)


# ---------------------------------------------------------------- 화면
@never_cache
@require_GET
@school_required
def index(request):
    """목록. 판 번호는 한 쿼리로 함께 가져온다(건수와 무관)."""
    rows = (Notice.objects.in_space(request.space).visible_to(request.member)
            .annotate(v=Max("versions__v")).order_by("-updated_at", "-pk"))
    page = Paginator(rows, PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "notices/list.html", _space_ctx(request, page="board", notices=page,
                                                           writable=request.space.school.writable))


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@school_write_required
@_body_guard
def new(request):
    if request.method == "GET" or request.upload_error:
        blank = {"title": "", "body": "", "aud": "all", "groups": [], "items": [{"name": k, "value": ""} for k in ITEMS]}
        if request.upload_error:
            return _form_page(request, blank, status=400, errors={"files": request.upload_error})
        return _form_page(request, blank)
    form, errors = _read_form(request.POST)
    files, error, status = _uploads(request)
    if error:
        errors["files"] = error
    if errors:
        return _form_page(request, form, status=status, errors=errors)
    me = request.member
    with transaction.atomic():
        n = Notice.objects.create(space=request.space, title=form["title"], by=me, by_name=me.name, group=me.group,
                                  audience=audience_value(form["groups"] if form["aud"] == "groups" else []))
        _save_files(n, files)
        _add_version(n, form, me.name)
    return redirect(f"/s/{request.space.token}/board/{n.key}/")


def _edit_form(n):
    """지금 판을 폼 값으로."""
    ver = _latest(n)
    groups = [g for g in n.audience.split(",") if g and g != "all"]
    return {"title": ver.fields[TITLE], "body": ver.body, "aud": "groups" if groups else "all", "groups": groups,
            "items": [{"name": k, "value": ver.fields.get(k, "")} for k in ITEMS]}, ver


def _edit_page(request, n, form, base_v, status=200, removing=(), **extra):
    current = list(n.files.order_by("at", "pk"))
    for f in current:
        f.removing = f.key in removing
    return _form_page(request, form, status=status, notice=n, base_v=base_v, current_files=current, **extra)


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@school_write_required
@_body_guard
def edit(request, key):
    n = _visible(request, key)
    if not _can_edit(request.member, n):
        return HttpResponseForbidden()
    if request.upload_error:
        form, ver = _edit_form(n)
        return _edit_page(request, n, form, ver.v, status=400, errors={"files": request.upload_error})
    if request.method == "GET":
        form, ver = _edit_form(n)
        return _edit_page(request, n, form, ver.v)
    form, errors = _read_form(request.POST)
    removing = request.POST.getlist("remove")
    files, error, status = _uploads(request)
    if error:
        errors["files"] = error
    if errors:
        return _edit_page(request, n, form, request.POST.get("base_v", ""), status=status, removing=removing, errors=errors)
    conflict = None
    with transaction.atomic():
        if Notice.objects.select_for_update().filter(pk=n.pk).first() is None:  # 고치는 사이 지워짐
            raise Http404
        latest = _latest(n)
        if request.POST.get("base_v") != str(latest.v):
            conflict = latest
        elif n.files.exclude(key__in=removing).count() + len(files) > MAX_FILES:
            errors["files"] = TOO_MANY_FILES
        else:
            n.title = form["title"]
            n.audience = audience_value(form["groups"] if form["aud"] == "groups" else [])
            n.files.filter(key__in=removing).delete()
            _save_files(n, files)
            if _add_version(n, form, request.member.name):
                n.save(update_fields=["title", "audience", "updated_at"])
    if conflict is not None:
        return _edit_page(request, n, form, conflict.v, status=409, removing=removing, conflict=conflict,
                          conflict_files=bool(files))
    if errors:
        return _edit_page(request, n, form, latest.v, status=400, removing=removing, errors=errors)
    return redirect(f"/s/{request.space.token}/board/{n.key}/")


@never_cache
@require_GET
@school_required
def detail(request, key):
    """상세. ?v=N이면 그 판을 서버가 다시 그린다. 바뀐 상자는 바로 앞 판과 비교."""
    n = _visible(request, key)
    history = list(n.versions.order_by("-v").values("v", "by", "at"))
    wanted = request.GET.get("v")
    if wanted is None:
        v = history[0]["v"]
    elif wanted.isascii() and wanted.isdigit() and len(wanted) < 10:
        v = int(wanted)
    else:
        raise Http404
    ver = get_object_or_404(n.versions, v=v)
    prev = n.versions.filter(v=v - 1).first()
    current = {f.key: f for f in n.files.order_by("at", "pk")}
    is_latest = v == history[0]["v"]
    # 받을 수 있는 파일은 지금 올라와 있는 것뿐. 옛 판은 그 판의 첨부 이름을 그때 순서대로, 없어진 것은 글자만.
    attach = ([{"name": f.name, "file": f} for f in current.values()] if is_latest else
              [{"name": f["name"], "file": current.get(f["key"])} for f in ver.fields.get(FILE_LIST, [])])
    ctx = _space_ctx(request, page="board", n=n, ver=ver, prev=prev, history=history, is_latest=is_latest,
                     latest_v=history[0]["v"], title=ver.fields.get(TITLE, n.title),
                     items=[{"name": k, "value": ver.fields[k]} for k in ITEMS if ver.fields.get(k)],
                     audience=ver.fields.get(AUDIENCE, n.audience_text), attach=attach,
                     can_edit=_can_edit(request.member, n) and request.space.school.writable)
    if prev is not None:
        ctx["changes"] = _changes(prev.fields, ver.fields)
        ctx["body_changed"] = prev.body != ver.body
        ctx["body_diff"] = _body_diff(prev.body, ver.body) if ctx["body_changed"] else []
    return render(request, "notices/detail.html", ctx)


@never_cache
@require_POST
@school_required
@school_write_required
def delete(request, key):
    n = _visible(request, key)
    if not _can_edit(request.member, n):
        return HttpResponseForbidden()
    n.delete()  # 판·첨부 행 CASCADE, 디스크 파일은 커밋 뒤 hwpfiles.retention
    return redirect(f"/s/{request.space.token}/board")


@never_cache
@require_GET
@school_required
def download(request, key, file_key):
    """내려받기: 공지를 볼 수 있는 회원만, 첨부로만, 캐시 금지."""
    f = get_object_or_404(_visible(request, key).files, key=file_key)
    try:
        fh = f.file.open("rb")
    except FileNotFoundError:
        log.warning("notice file missing file=%s", f.pk)
        raise Http404
    resp = FileResponse(fh, as_attachment=True, filename=f.name, content_type=TYPES[f.kind])
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


