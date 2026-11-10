"""I1 공지 게시판(prototype board·noticeDetail): 쓰기·목록·상세·판 보기·첨부. I2 확인·현황·실질 변경·쪽지 링크(현황·🔔 계산은 notices.seen).

- 보기는 NoticeQuerySet.visible_to로만(대상 밖이면 상세·옛 판·첨부 모두 404). 고치기·지우기는 쓴 사람·관리자, 직위 글자는 안 본다.
- 충돌: 폼이 고칠 때의 판(base_v)을 들고 다니다가, 저장 때 최신 판과 다르면 저장하지 않고 쓴 내용과 함께 돌려준다(409).
- 첨부 검사는 filekinds.judge(P4: 한글·PDF·오피스·오픈문서·그림, 압축 안 풂·매크로 거부). 서버는 첨부를 풀지 않는다.
- 요청 크기: CSRF 미들웨어가 뷰보다 먼저 본문을 풀기 때문에, 쓰기·고치기는 _body_guard로 CSRF 검사를 뷰 앞으로 옮겨
  선언 크기를 본문보다 먼저 본다.
- 제목·본문은 자동 이스케이프, 본문 줄바꿈만 linebreaksbr.
- 봄 기록은 브라우저가 2초 보거나 끝까지 내린 뒤 보내는 POST로만(notice_seen.js). 받는 사람 응답에 남의 확인 여부를 싣지 않는다.
- 확인 필수는 Member.is_manager만 켠다. 그 공지의 받는 곳도 관리자만 바꾸고, 내용을 바꾸면 실질 변경이 강제된다.
- 만료된 학교도 봄 기록·확인과 관리자의 판 내용 지우기(개인정보 조치)는 받는다.
"""
import difflib
import logging
import os
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import TooManyFilesSent
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotFound
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe

import hwp
from config.ratelimit import TOO_MANY, allow
from hwpfiles.models import storage_room
from hwpfiles.views import _clean_name
from collect.models import Notification
from repairs.models import Repair
from spaces.gates import manager_required, school_required, school_write_required
from spaces.models import Member
from spaces.views import _space_ctx

from . import seen as seenlib
from .filekinds import ACCEPT, ALIAS, EMBED, LABEL, MACRO, TYPES, judge
from .models import AUDIENCE, FILE_LIST, FILES, ITEMS, MUST, TITLE, Notice, NoticeFile, NoticeSeen, NoticeVersion, audience_value, version_label
from .seen import changes as _changes

log = logging.getLogger(__name__)
MB = 1024 * 1024
PER_PAGE = 20
MAX_TITLE, MAX_ITEM, MAX_BODY = 200, 200, 10000
MAX_FILES, MAX_FILE = 5, 20 * MB
# 화면 안내를 주는 선언 크기 상한. 공지 본문 상한(settings.MAX_NOTICE_REQUEST_BYTES, 413)보다 1MB 아래라 이 사이는 폼으로 돌아온다
MAX_REQUEST = settings.MAX_NOTICE_REQUEST_BYTES - MB
KINDS_TEXT = "한글·PDF·엑셀·워드·파워포인트·오픈문서·그림"  # 거부 안내(P4)
KINDS_SHORT = "한글·PDF·오피스·그림"  # 첨부 칸 안내 한 줄(1536×750에서 파일 고르기 단추가 보이게)
UPLOAD_PER_HOUR = 30  # 회원 한 명이 시간당 첨부를 담아 저장하는 횟수(거부된 것도 센다)
TOO_BIG = f"첨부는 합쳐서 {MAX_FILES * MAX_FILE // MB}MB까지 올릴 수 있어요. 파일을 줄여 다시 올려 주세요. 방금 쓴 내용은 저장되지 않았어요."
BAD_UPLOAD = "올린 내용을 읽지 못했어요. 파일 이름이 너무 길거나 파일이 너무 많아요. 이름을 줄여 다시 올려 주세요. 방금 쓴 내용은 저장되지 않았어요."
TOO_MANY_FILES = f"첨부는 공지 하나에 {MAX_FILES}개까지예요."
QUOTA = "학교 저장 공간이 가득 찼어요. 보관 기간이 지난 파일이 지워진 뒤 다시 올려 주세요."
TOTAL_QUOTA = "고티처 서비스 저장 공간이 가득 찼어요. 잠시 뒤 다시 올려 주세요. 계속되면 운영자에게 알려 주세요."
FREE_QUOTA = "무료로 쓸 수 있는 저장 공간이 서비스 전체에서 가득 찼어요. 잠시 뒤 다시 올려 주세요. 계속되면 운영자에게 알려 주세요."
MUST_ONLY = "확인 필수는 학교 관리자만 켤 수 있어요."
AUD_ONLY = "확인 필수 공지의 받는 곳은 학교 관리자만 바꿀 수 있어요."
TOAST = "notice_toast"  # 세션: 목록으로 옮긴 뒤 한 번 띄울 저장 결과


def _visible(request, key):
    return get_object_or_404(Notice.objects.in_space(request.space).visible_to(request.member), key=key)


def _can_edit(member, n):
    return member.is_manager or n.by_id == member.pk


def _latest(n):
    return n.versions.order_by("-v").first()


def _note_tail(prev, ver):
    """쪽지 문구 꼬리: ' (2차 수정: 장소·일시 변경)'. 실질 변경 판일 때만."""
    if prev is None or prev.erased or not ver.real:
        return ""
    names = [c["name"] for c in _changes(prev.fields, ver.fields)] + (["본문"] if prev.body != ver.body else [])
    return f" ({ver.label}: {'·'.join(names)} 변경)"


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


def _read_form(data, space):
    """제출 값 → (폼 값, 오류). 저장할 값도 이 폼 값이다. 받는 그룹은 이 공간의 소속 값만(기관은 그 기관 그룹 키만, T2)."""
    keys = space.group_keys if space.is_org else Member.Group.values
    form = {"title": _clean(data, "title"), "body": _clean(data, "body"), "must": "must" in data, "real": "real" in data,
            "items": [{"name": k, "value": _clean(data, k)} for k in ITEMS],
            "aud": "groups" if data.get("aud") == "groups" else "all",
            "groups": [g for g in keys if g in data.getlist("groups")]}
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
        errors["aud"] = "받을 소속을 하나 이상 골라 주세요."
    return form, errors


def _read_files(uploads):
    """올린 파일 → ([{name, kind, size, file}], 오류). 이름의 확장자와 내용이 같은 형식일 때만 받는다."""
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
        ext = ALIAS.get(ext, ext)
        if ext not in TYPES:
            return [], f"「{name}」: {KINDS_TEXT}만 올릴 수 있어요."
        try:
            kind = judge(f)
        except hwp.UnsafeFile as e:
            if str(e) == MACRO:
                return [], f"「{name}」: 매크로가 든 문서는 받지 않아요. 매크로 없는 형식(.xlsx·.docx·.pptx 등)으로 저장해 올려 주세요."
            if str(e) == EMBED:
                return [], f"「{name}」: 외부 파일 연결이나 넣은 개체(OLE)가 든 문서는 받지 않아요. 연결·개체를 빼고 저장해 올려 주세요."
            return [], f"「{name}」: 파일 구조가 안전하지 않아 받지 않았어요. 다시 저장해 올려 주세요."
        except hwp.HwpError:
            kind = ""
        if kind != ext:
            return [], f"「{name}」: 파일 이름과 내용이 달라요. 진짜 {LABEL[ext]} 파일만 올릴 수 있어요."
        out.append({"name": name, "kind": kind, "size": f.size, "file": f})
    return out, ""


def _quota_error(space, files):
    """저장 한도 안내 한 줄(공지·수리·식판). 넣는 양이 0이면 넘친 공간도 거부하지 않는다."""
    size = sum(f["size"] for f in files)
    if not size:
        return ""
    room, why = storage_room(space)
    return {"Quota": QUOTA, "FreeQuota": FREE_QUOTA, "TotalQuota": TOTAL_QUOTA}[why] if size > room else ""


def _uploads(request):
    """첨부와 빈도 제한. (파일들, 오류, 오류일 때 상태 코드). 빈도는 파일을 읽기 전에 센다."""
    uploads = request.FILES.getlist("attach")
    if not uploads:
        return [], "", 400
    if not allow("notice-up", request.member.key, UPLOAD_PER_HOUR, 3600):
        return [], TOO_MANY, 429
    files, error = _read_files(uploads)
    return files, error or _quota_error(request.space, files), 400


def _add_version(n, form, by, real=False):
    """지금 공지 상태로 판 하나. 변경이 없으면 아무것도 하지 않는다. n.audience·첨부 행은 이미 바뀌어 있어야 한다."""
    files = list(n.files.order_by("at", "pk").values("key", "name"))
    fields = {TITLE: form["title"], **{i["name"]: i["value"] for i in form["items"]},
              AUDIENCE: n.audience_text, FILES: ", ".join(f["name"] for f in files), FILE_LIST: files,
              **({MUST: "켬"} if n.must else {})}
    latest = _latest(n)
    if latest is not None and latest.fields == fields and latest.body == form["body"]:
        return None
    return NoticeVersion.objects.create(notice=n, v=latest.v + 1 if latest else 1, fields=fields, body=form["body"], by=by, real=real)


def _save_files(n, files):
    """트랜잭션 안에서 부른다. 되돌려지면 디스크에 남은 파일은 다음 purge의 고아 정리가 줍는다. 큰 파일은 임시 파일째 옮긴다."""
    for f in files:
        row = NoticeFile(notice=n, name=f["name"], kind=f["kind"], stored=f["size"])
        row.file.save(f["kind"], f["file"])


def _must(request, current=False):
    """저장할 확인 필수 값. 관리자는 보낸 대로, 일반 회원은 지금 값 그대로. 일반 회원이 새로 켜려 하면 None(거부)."""
    asked = "must" in request.POST
    if request.member.is_manager:
        return asked
    return None if asked and not current else current


def _form_ctx(request, form):
    """입력 칸 조각(_form_fields.html)이 쓰는 값. 쓰기 대화상자와 고치기 화면이 같다."""
    picked = set(form["groups"])
    return {"form": form, "writable": request.space.school.writable, "max_files": MAX_FILES, "can_must": request.member.is_manager,
            "max_mb": MAX_FILE // MB, "max_total_mb": MAX_FILES * MAX_FILE // MB, "accept": ACCEPT, "kinds_text": KINDS_SHORT,
            "group_opts": [{"id": g, "label": label, "on": g in picked}
                           for g, label in (request.space.group_choices if request.space.is_org else Member.Group.choices)]}


def _form_page(request, form, status=200, **extra):
    """고치기 화면. 새 공지는 목록 위 대화상자(_list_page)."""
    return render(request, "notices/form.html", _space_ctx(request, page="board", **_form_ctx(request, form), **extra), status=status)


def _blank_form():
    return {"title": "", "body": "", "aud": "all", "groups": [], "items": [{"name": k, "value": ""} for k in ITEMS]}


# ---------------------------------------------------------------- 화면
def _list_page(request, form=None, status=200, **extra):
    """목록. 판 번호는 한 쿼리로 함께 가져온다(건수와 무관). 쓰기 대화상자가 함께 그려지고, open_form이면 열린 채로."""
    rows = (Notice.objects.in_space(request.space).visible_to(request.member).select_related("space")  # 받는 곳 이름(space.group_names)
            .annotate(v=Max("versions__v")).order_by("-updated_at", "-pk"))
    page = Paginator(rows, PER_PAGE).get_page(request.GET.get("page"))
    me = request.member
    shown = list(page.object_list)
    page.object_list = shown
    stats = seenlib.statuses(request.space, [n for n in shown if seenlib.can_see_status(n, me)])
    acked = seenlib.acked_ids(me, [n for n in shown if n.must])
    for n in shown:
        n.stat = stats.get(n.pk)
        n.todo = n.must and n.pk not in acked and n.by_id != me.pk and seenlib.in_audience(n, me)
    repair_n = Repair.objects.in_space(request.space).filter(cat=Repair.Cat.FACILITY).exclude(status=Repair.Status.DONE).count()
    return render(request, "notices/list.html", _space_ctx(request, page="board", notices=page, repair_n=repair_n,
                                                           **_form_ctx(request, form or _blank_form()), **extra), status=status)


@never_cache
@require_GET
@school_required
def index(request):
    return _list_page(request, toast=request.session.pop(TOAST, ""))


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@school_write_required
@_body_guard
def new(request):
    if request.upload_error:
        return _list_page(request, status=400, open_form=True, errors={"files": request.upload_error})
    if request.method == "GET":
        return _list_page(request, open_form=True)
    must = _must(request)
    if must is None:
        return HttpResponseForbidden(MUST_ONLY)
    form, errors = _read_form(request.POST, request.space)
    files, error, status = _uploads(request)
    if error:
        errors["files"] = error
    if errors:
        return _list_page(request, form, status=status, open_form=True, errors=errors)
    me = request.member
    with transaction.atomic():
        n = Notice.objects.create(space=request.space, title=form["title"], by=me, by_name=me.name, group=me.group, must=must,
                                  audience=audience_value(form["groups"] if form["aud"] == "groups" else []))
        _save_files(n, files)
        _add_version(n, form, me.name)
        if must:
            seenlib.bells_for_save(request.space, n, me)
    request.session[TOAST] = "공지를 올렸어요."
    return redirect(f"/s/{request.space.token}/board")


def _edit_form(n):
    """지금 판을 폼 값으로."""
    ver = _latest(n)
    groups = [g for g in n.audience.split(",") if g and g != "all"]
    return {"title": ver.fields[TITLE], "body": ver.body, "aud": "groups" if groups else "all", "groups": groups, "must": n.must,
            "real": False, "items": [{"name": k, "value": ver.fields.get(k, "")} for k in ITEMS]}, ver


def _aud_locked(request, n):
    return n.must and not request.member.is_manager


def _edit_page(request, n, form, base_v, status=200, removing=(), **extra):
    current = list(n.files.order_by("at", "pk"))
    for f in current:
        f.removing = f.key in removing
    newest = _latest(n)  # 항목이 이 값과 달라지면 화면이 '실질 변경'을 켠다(notice_form.js)
    for i in form["items"]:
        i["orig"] = newest.fields.get(i["name"], "")
    return _form_page(request, form, status=status, notice=n, base_v=base_v, current_files=current, orig_body=newest.body,
                      aud_locked=_aud_locked(request, n), **extra)


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
    must = _must(request, n.must)
    if must is None:
        return HttpResponseForbidden(MUST_ONLY)
    form, errors = _read_form(request.POST, request.space)
    if _aud_locked(request, n):  # 잠긴 칸은 제출되지 않는다. 다른 값을 보내면 거부
        if "aud" in request.POST and audience_value(form["groups"] if form["aud"] == "groups" else []) != n.audience:
            return HttpResponseForbidden(AUD_ONLY)
        current = _edit_form(n)[0]
        form["aud"], form["groups"] = current["aud"], current["groups"]
        errors.pop("aud", None)
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
            n.must = must
            # 확인 필수 공지는 내용을 바꾸면 언제나 실질 변경(받는 사람이 모르는 채 확인 수가 유지되지 않게)
            changed = latest.body != form["body"] or any(latest.fields.get(i["name"], "") != i["value"] for i in form["items"])
            ver = _add_version(n, form, request.member.name, real=form["real"] or (must and changed))
            if ver:  # 켬·끔도 fields에 들어가 판이 오른다
                n.save(update_fields=["title", "audience", "must", "updated_at"])
                seenlib.drop_unviewable_bells(request.space, n)
                seenlib.bells_for_save(request.space, n, request.member, ver, latest)
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
    history = [{**h, "label": version_label(h["v"])} for h in n.versions.order_by("-v").values("v", "by", "at", "erased")]
    wanted = request.GET.get("v")
    if wanted is None:
        v = history[0]["v"]
    elif wanted.isascii() and wanted.isdigit() and len(wanted) < 10:
        v = int(wanted)
    else:
        raise Http404
    ver = get_object_or_404(n.versions, v=v)
    prev = n.versions.filter(v=v - 1).first()
    if prev is not None and (prev.erased or ver.erased):  # 지운 판의 값이 바뀐 부분 상자로 나오지 않게
        prev = None
    current = {f.key: f for f in n.files.order_by("at", "pk")}
    is_latest = v == history[0]["v"]
    # 받을 수 있는 파일은 지금 올라와 있는 것뿐. 옛 판은 그 판의 첨부 이름을 그때 순서대로, 없어진 것은 글자만.
    attach = ([{"name": f.name, "file": f} for f in current.values()] if is_latest else
              [{"name": f["name"], "file": current.get(f["key"])} for f in ver.fields.get(FILE_LIST, [])])
    me = request.member
    mine = list(NoticeSeen.objects.filter(notice=n, member=me).values_list("v", "acked"))
    newest = ver if is_latest else n.versions.get(v=history[0]["v"])
    newest_prev = prev if is_latest else n.versions.filter(v=newest.v - 1).first()
    url, tail = f"{settings.SITE_URL}/n/{n.key}", _note_tail(newest_prev, newest)
    ctx = _space_ctx(request, page="board", n=n, ver=ver, prev=prev, history=history, is_latest=is_latest,
                     send_seen=is_latest and all(v != ver.v for v, _ in mine), acked=any(a for _, a in mine),
                     needs_ack=n.must and n.by_id != me.pk and seenlib.in_audience(n, me),
                     note_text=f"[고티처] {n.title}{tail}\n{url}",
                     status=seenlib.statuses(request.space, [n])[n.pk] if seenlib.can_see_status(n, me) else None,
                     remind_text=f"[고티처] 「{n.title}」 확인 부탁드려요.{tail}\n{url}",
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
def seen(request, key):
    """봄 기록. 브라우저가 2초 보거나 끝까지 내린 뒤 보낸다. v가 없으면 최신 판."""
    n = _visible(request, key)
    newest = _latest(n).v
    raw = request.POST.get("v", str(newest))
    if not (raw.isascii() and raw.isdigit() and len(raw) < 10):
        return HttpResponseBadRequest()
    v = int(raw)
    if not n.versions.filter(v=v).exists():
        raise Http404
    NoticeSeen.objects.get_or_create(notice=n, member=request.member, v=v)
    if v == newest:  # 다 봤으니 '바뀜' 🔔은 읽은 것으로(다음 묶음 알림에 다시 안 나가게)
        (Notification.objects.in_space(request.space)
         .filter(member=request.member, notice=n, kind=Notification.Kind.CHANGE, read=False).update(read=True))
    return HttpResponse(status=204)


@never_cache
@require_POST
@school_required
def ack(request, key):
    """확인 필수의 '확인했어요'. 열어 본 것과 구분해 acked로 남기고 이 공지의 🔔을 읽음으로 한다."""
    n = _visible(request, key)
    if not n.must:
        return HttpResponseBadRequest()
    NoticeSeen.objects.update_or_create(notice=n, member=request.member, v=_latest(n).v, defaults={"acked": True})
    Notification.objects.in_space(request.space).filter(member=request.member, notice=n, read=False).update(read=True)
    r = redirect(f"/s/{request.space.token}/board/{n.key}/")
    r.status_code = 303
    return r


@never_cache
@require_POST
@school_required
@manager_required
def erase(request, key, v):
    """옛 판 내용 지우기(실수로 쓴 개인정보): 관리자만, 최신 판은 그냥 고치면 되니 대상 아님. 🔔 문구의 옛값·새값도 함께 지운다."""
    n = _visible(request, key)
    ver = get_object_or_404(n.versions, v=v)
    if v == _latest(n).v:
        return HttpResponseBadRequest("최신 판은 고치기로 바꿔 주세요.")
    with transaction.atomic():
        ver.fields, ver.body, ver.erased = {}, "", True
        ver.save(update_fields=["fields", "body", "erased"])
        seenlib.blank_bells(request.space, n)
    r = redirect(f"/s/{request.space.token}/board/{n.key}/?v={v}")
    r.status_code = 303
    return r


@never_cache
@require_safe
def link(request, token):
    """쪽지 링크 /n/<token>(공지 key). 익명은 찾아보기 전에 로그인으로, 회원·대상은 상세로, 그 밖은 404. 모든 응답 noindex."""
    if not request.user.is_authenticated:
        resp = redirect("/start?" + urlencode({"next": f"/n/{token}"}, safe="/"))
    else:
        n = Notice.objects.select_related("space").filter(key=token).first()
        member = Member.objects.in_space(n.space).filter(user=request.user).first() if n else None
        if member and Notice.objects.in_space(n.space).visible_to(member).filter(pk=n.pk).exists():
            resp = redirect(f"/s/{n.space.token}/board/{n.key}/")
        else:
            resp = HttpResponseNotFound("찾을 수 없어요.")
    resp["X-Robots-Tag"] = "noindex, nofollow"
    return resp


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


