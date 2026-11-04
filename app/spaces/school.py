"""H3 학교 화면(prototype school·editMe): 학년분장·그룹 카드, 내 그룹·직책 고치기, 관리자 지정, 업무분장 붙여넣기. L1 요약 카드는 summary.py.

모든 교직원이 같은 화면을 본다. 권한은 Member.is_manager로만 판단하고, 그룹·직책 글자는 표시에만 쓴다.
"""
import re

from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import is_guest
from collect.paste import PasteError, html_to_tsv

from .duty_paste import EMPTY, TOO_BIG, read_duties
from .gates import manager_required, school_required, school_write_required
from .merge import remove_manager
from .models import Attendance, Duty, Member, MemberLog, Space, get_in_space_or_404
from .views import GROUP_ROLES, _cls_label, _invite, _space_ctx, log_place, place_values, read_place

LOG_N = 20  # 관리자에게 보이는 최근 이력
NAMES = re.compile(r"[,，、/\n]+")  # 담당 칸 하나에 여러 이름
TOAST = "school_toast"  # 세션: 화면을 옮긴 뒤 한 번 띄울 저장 결과


def _order(m):
    """직책 순, 교장 맨 앞. 학년은 1반·2반…·전담, 목록 밖 직책은 뒤(prototype school). 같으면 들어온 순서."""
    if m.position == "교장":
        return (-1, 0)
    if m.in_grade:
        tail = m.cls.partition("-")[2]
        return (0, int(tail)) if tail.isdecimal() else (1, 0)
    roles = GROUP_ROLES.get(m.group, [])
    return (0, roles.index(m.position)) if m.position in roles else (1, 0)


def _group_cards(rows):
    """사람이 있는 그룹의 카드, 빈 그룹 이름. rows: _roster의 줄."""
    cards, empty = [], []
    for g, label in Member.Group.choices:
        mine = sorted((r for r in rows if r["m"].group == g), key=lambda r: _order(r["m"]))
        if not mine:
            empty.append(label)
            continue
        cards.append({"label": label, "n": len(mine),
                      "rows": [{**r, "pos": _cls_label(r["m"].cls) if r["m"].in_grade else r["m"].position} for r in mine]})
    return cards, empty


def _person(text):
    """명부와 비교할 이름: 괄호 부분을 떼고 공백을 뺀다('김 서 연'·'김서연(부장)' → '김서연')."""
    return re.sub(r"\s+", "", re.sub(r"[(（][^)）]*[)）]?", "", text))


def split_people(who, known):
    """담당 칸 → 사람들(붙인 이름, 순서대로 한 번씩). 쉼표·/·줄바꿈으로 나누고, 공백으로 나눈 조각이 모두 명부 이름과
    정확히 같을 때만 공백으로도 나눈다('오일 오이'는 두 사람, '김 서 연'·'부장 김철수'는 한 사람). known: 명부의 _person 이름들."""
    out = []
    for part in NAMES.split(who):
        pieces = re.sub(r"[(（][^)）]*[)）]?", "", part).split()
        if len(pieces) > 1 and all(p in known for p in pieces):
            out += pieces
        else:
            out.append(_person(part))
    return list(dict.fromkeys(n for n in out if n))


def _name_groups(space):
    """{이름: [그룹 이름들]}. 같은 이름이 둘이면 둘 다 보여 준다."""
    found = {}
    for m in Member.objects.in_space(space).only("name", "group"):
        labels = found.setdefault(_person(m.name), [])
        if m.get_group_display() not in labels:
            labels.append(m.get_group_display())
    return found


def _pair(who, known, me=None):
    """담당 글자를 명부와 짝짓는다(FK 없음). 쉼표·/·줄바꿈으로 나눈 사람마다 명부에 있는지 본다."""
    people = split_people(who, known)
    groups = dict.fromkeys(g for n in people for g in known.get(n, []))
    return {"people": people, "groups": " · ".join(groups), "unknown": [n for n in people if n not in known],
            "me": me is not None and _person(me.name) in people}


def _dept_cards(space, me):
    """부서마다 카드, 부서는 표에 나온 순서. 사람 수는 담당 칸을 나눈 사람 기준."""
    known, depts = _name_groups(space), {}
    for d in Duty.objects.in_space(space).order_by("order", "pk"):
        depts.setdefault(d.dept, []).append({"d": d, **_pair(d.who, known, me)})
    return [{"dept": dept, "rows": rows, "people": len({n for r in rows for n in r["people"]})}
            for dept, rows in depts.items()]


def _school_ctx(request, **extra):
    space, me = request.space, request.member
    tab = "duty" if request.GET.get("tab") == "duty" else "grade"
    ctx = _space_ctx(request, page="school", tab=tab, writable=space.school.writable, **extra)
    ctx.update(_invite(space, me))
    ctx["n_groups"] = len(Member.Group.choices)
    ctx["alone"] = me.is_manager and len(ctx["rows"]) == 1
    ctx["plan_left"] = space.school.days_left if me.is_manager else None
    ctx["attendance"] = list(Attendance.objects.in_space(space).filter(date=timezone.localdate()).select_related("member").order_by("pk"))
    ctx["n_attendance"] = len({a.name for a in ctx["attendance"]})
    ctx["can_attend"] = (me.is_manager or me.can_duty) and space.school.writable
    from .summary import figures  # 여기서 부른다: collect.recipients가 이 모듈을 import한다(순환)
    ctx["sum"] = figures(space)
    if tab == "duty":
        ctx["dept_cards"] = _dept_cards(space, me)
        ctx["n_tasks"] = sum(len(c["rows"]) for c in ctx["dept_cards"])
    else:
        ctx["group_cards"], ctx["empty_groups"] = _group_cards(ctx["rows"])
        if me.is_manager:
            ctx["logs"] = MemberLog.objects.in_space(space).order_by("-at", "-pk")[:LOG_N]
    return ctx


def school_body(request, status=200, **extra):
    """학교 화면 본문만(htmx). 관리자 지정·내보내기 뒤에 버튼과 초대 카드까지 함께 맞도록 통째로 준다."""
    return render(request, "spaces/_school.html", _school_ctx(request, **extra), status=status)


@never_cache
@require_GET
@school_required
def school(request):
    return render(request, "spaces/school.html", _school_ctx(request, toast=request.session.pop(TOAST, "")))


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@school_write_required
def edit_me(request):
    """내 그룹·직책(승인 없음, 학교 입장과 같은 검증). 그룹을 바꾸면 cls·position도 함께 맞춘다. 실제로 바뀌면 이력."""
    me, post = request.member, request.method == "POST"
    if post:
        data = request.POST
    elif "group" in request.GET:
        data = request.GET
    else:
        listed = me.position in GROUP_ROLES.get(me.group, [])
        data = {"group": me.group, "cls": me.cls, "role": me.position if listed else "",
                "role_custom": "" if listed or me.in_grade else me.position}
    place, errors = read_place(request.space, data, post)
    if post and not errors:
        before = me.cls_text
        me.group, me.cls, me.position = place_values(place)
        me.save(update_fields=["group", "cls", "position"])
        log_place(me, before)
        if before != me.cls_text:
            request.session[TOAST] = f"바꿨어요: {me.cls_text}"
        return redirect(f"/s/{request.space.token}/school")
    form = {**place, "groups": Member.Group.choices, "errors": errors, "edit": True,
            "form_url": f"/s/{request.space.token}/school/me"}
    if request.headers.get("HX-Request"):
        return render(request, "spaces/_join_school_form.html", form)
    ctx = _space_ctx(request, page="school", writable=request.space.school.writable, **form)
    return render(request, "spaces/school_me.html", ctx, status=400 if errors else 200)


@never_cache
@require_POST
@school_required
@manager_required
@school_write_required
def set_manager(request, key):
    """관리자 지정·해제. 구글 회원만 지정, 마지막 관리자는 해제 거부. 직위 글자는 보지 않는다."""
    m = get_in_space_or_404(Member, request, key=key)
    on, error = request.POST.get("on") == "1", ""
    if on != m.is_manager:
        if on and (m.user_id is None or is_guest(m.user)):
            error = "이름만 들어온 선생님은 관리자가 될 수 없어요. 구글 계정을 이으면 돼요."
        elif on:
            Member.objects.filter(pk=m.pk).update(is_manager=True)
        else:
            try:
                remove_manager(m)
            except ValueError as e:
                error = str(e)
        if not error:
            MemberLog.objects.create(space=request.space, by=request.member.name, name=m.name,
                                     before="관리자" if not on else "관리자 아님", after="관리자" if on else "관리자 아님")
    if not request.headers.get("HX-Request"):
        if error:
            return render(request, "spaces/school.html", _school_ctx(request, error=error), status=400)
        return redirect(f"/s/{request.space.token}/school")
    if error:
        return school_body(request, status=400, error=error)
    done = f"{m.name} 선생님을 관리자로 세웠어요." if on else f"{m.name} 선생님을 관리자에서 뺐어요."
    if request.member.pk == m.pk:
        request.member.is_manager = on
    return school_body(request, toast=done)


def _preview_rows(rows, space):
    known = _name_groups(space)
    out = [{**r, **_pair(r["who"], known)} for r in rows]
    return out, sorted({n for r in out for n in r["unknown"]})


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@manager_required
@school_write_required
def duties(request):
    """업무분장 붙여넣기 화면과 반영(통째로 바꾸기). 값은 반영할 때 서버가 다시 읽는다."""
    space, error, paste = request.space, "", ""
    if request.method == "POST":
        paste = request.POST.get("paste", "")
        try:
            rows = read_duties(paste)["rows"]
        except PasteError as e:
            error = str(e)
        else:
            with transaction.atomic():
                Space.objects.select_for_update().filter(pk=space.pk).first()  # 관리자 둘이 동시에 바꿔도 한 표만 남게
                Duty.objects.in_space(space).delete()
                Duty.objects.bulk_create(Duty(space=space, order=i, **r) for i, r in enumerate(rows))
            return redirect(f"/s/{space.token}/school?tab=duty")
    ctx = _space_ctx(request, page="school", writable=space.school.writable, paste=paste, error=error,
                     n_now=Duty.objects.in_space(space).count())
    return render(request, "spaces/duties.html", ctx, status=400 if error else 200)


@never_cache
@require_POST
@school_required
@manager_required
@school_write_required
def duties_preview(request):
    """붙여넣기 미리보기(htmx). 저장하지 않는다. html이 오면(한글·엑셀 클립보드) TSV로 바꿔 입력 칸에 넣도록 함께 돌려준다."""
    html, ctx = request.POST.get("html", ""), {}
    try:
        tsv = html_to_tsv(html, too_big=TOO_BIG) if html else ""
        if html and not tsv:
            raise PasteError("붙여넣은 내용에서 표를 찾지 못했어요. 표를 드래그해 다시 복사해 주세요.")
        text = tsv or request.POST.get("paste", "")
        if not text.strip():
            raise PasteError(EMPTY)
        d = read_duties(text)
    except PasteError as e:
        ctx["error"] = str(e)
    else:
        rows, unknown = _preview_rows(d["rows"], request.space)
        ctx.update(tsv=tsv, rows=rows, unknown=unknown, skipped=d["skipped"], dropped=d["dropped"],
                   n_depts=len({r["dept"] for r in rows}), n_now=Duty.objects.in_space(request.space).count())
    return render(request, "spaces/_duty_out.html", ctx)
