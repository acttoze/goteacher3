"""H3 학교 화면(prototype school·editMe): 학년분장·그룹 카드, 내 그룹·직책 고치기, 관리자 지정, 업무분장 붙여넣기. L1 요약 카드는 summary.py.
Q2 관리자 순서·소속 바꾸기(arrange): 소속·부서 카드 순서, 카드 안 순서, 다른 소속·부서로 옮기기.
U1 소속 색(group_color): 바꾸기 화면의 소속 카드에서 관리자가 팔레트 중 하나를 고른다. 일정 카드의 점 색이 된다.

모든 교직원이 같은 화면을 본다. 권한은 Member.is_manager로만 판단하고, 그룹·직책 글자는 표시에만 쓴다.
"""
import json
import re
import secrets

from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import DeviceToken, PasswordChangeRequired, is_guest, is_id_member
from collect.models import Notification
from collect.paste import PasteError, html_to_tsv

from .duty_paste import EMPTY, TOO_BIG, read_duties
from .gates import manager_required, school_required, school_write_required
from .merge import remove_manager
from .models import GROUP_PALETTE, Attendance, Duty, Member, MemberLog, Space, get_in_space_or_404
from .views import GROUP_ROLES, _cls_label, _grade_choices, _invite, _josa, _space_ctx, log_place, place_values, read_place

LOG_N = 20  # 관리자에게 보이는 최근 이력
NAMES = re.compile(r"[,，、/\n]+")  # 담당 칸 하나에 여러 이름
TOAST = "school_toast"  # 세션: 화면을 옮긴 뒤 한 번 띄울 저장 결과
TEMP_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"  # 임시 비밀번호: 불러 주고 받아 적기 쉽게(l·o·0·1 뺌)


def _order(m):
    """직책 순, 교장 맨 앞. 학년은 1반·2반…·전담, 목록 밖 직책은 뒤(prototype school). 같으면 들어온 순서."""
    if m.position == "교장":
        return (-1, 0)
    if m.in_grade:
        tail = m.cls.partition("-")[2]
        return (0, int(tail)) if tail.isdecimal() else (1, 0)
    roles = GROUP_ROLES.get(m.group, [])
    return (0, roles.index(m.position)) if m.position in roles else (1, 0)


def _in_group(m):
    """소속 안 순서(Q2): 관리자가 정한 순서, 정하지 않은 사람은 그 뒤에 직책 순."""
    return (m.order is None, m.order or 0, _order(m))


def sort_members(space, members):
    """학교 소속순(Q2): 이 학교의 소속 순서, 소속 안은 _in_group. 학교 화면과 취합 받는 사람 소속순이 같이 쓴다."""
    rank = {g: i for i, (g, _) in enumerate(space.group_choices)}
    members.sort(key=lambda m: (rank.get(m.group, len(rank)), _in_group(m)))
    return members


def _group_cards(space, rows, arrange=False):
    """사람이 있는 그룹의 카드, 빈 그룹 이름. rows: _roster의 줄. 바꾸기 화면(arrange)은 빈 소속도 카드로(옮겨 넣을 곳)."""
    cards, empty = [], []
    colors = space.group_colors
    for g, label in space.group_choices:
        mine = sorted((r for r in rows if r["m"].group == g), key=lambda r: _in_group(r["m"]))
        if not mine and not arrange:
            empty.append(label)
            continue
        cards.append({"g": g, "label": label, "n": len(mine), "color": colors[g], "hex": GROUP_PALETTE[colors[g]][0],
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
    arrange = me.is_manager and space.school.writable and request.GET.get("arrange") == "1"
    if request.GET.get("err") == "1":
        extra.setdefault("error", "저장하지 못해 원래 자리로 돌렸어요. 다시 해 주세요.")
    ctx = _space_ctx(request, page="school", tab=tab, writable=space.school.writable, arrange=arrange, **extra)
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
        ctx["group_cards"], ctx["empty_groups"] = _group_cards(space, ctx["rows"], arrange)
        if arrange:
            ctx["palette"] = [(k, name) for k, (_, name) in GROUP_PALETTE.items()]
        if arrange:  # 학년으로 옮길 때 고를 반
            ctx["grade_classes"] = [(g, [(c, _cls_label(c)) for c in _grade_choices(space.school, int(g[1:]))])
                                    for g, _ in space.group_choices if g[1:].isdigit()]
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
    if request.headers.get("HX-Request"):  # 바꾸기 저장이 실패하면 본문을 다시 받아 원래 자리로(Q2)
        return school_body(request)
    return render(request, "spaces/school.html", _school_ctx(request, toast=request.session.pop(TOAST, "")))


def _keys(request):
    """keys: 놓은 뒤 그 칸의 순서(JSON 글자 목록). 이상하면 None."""
    try:
        keys = json.loads(request.POST.get("keys", ""))
    except ValueError:
        return None
    ok = isinstance(keys, list) and len(keys) <= 500 and all(isinstance(k, str) for k in keys)
    return list(dict.fromkeys(keys)) if ok else None


def _move_member(request, group, keys):
    """소속 카드 안 순서(keys = 그 소속의 놓은 뒤 순서). 다른 소속에서 온 사람(한 번에 한 명)은 소속을 바꾸고 이력·🔔.
    거절은 ValueError(안내 문구).
    학년으로 오면 반(cls)을 받고 직위는 반 이름, 학년 밖으로 가면 cls는 소속 이름, 직위는 그대로(학년 반 이름이었으면 비움)."""
    space, me = request.space, request.member
    by_key = Member.objects.in_space(space).in_bulk(keys, field_name="key")
    picked = [by_key[k] for k in keys if k in by_key]
    moved = [m for m in picked if m.group != group]
    if len(moved) > 1:
        raise ValueError("한 번에 한 선생님씩 옮겨 주세요.")
    done = "순서를 바꿨어요."
    if moved:
        m, label = moved[0], Member.Group(group).label
        before = m.cls_text
        if group[1:].isdigit():
            cls = request.POST.get("cls", "")
            if cls not in _grade_choices(space.school, int(group[1:])):
                raise ValueError("몇 반인지 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요.")
            m.cls, m.position = cls, _cls_label(cls)
        else:
            m.cls, m.position = label, "" if m.in_grade else m.position
        m.group = group
        m.save(update_fields=["group", "cls", "position"])
        MemberLog.objects.create(space=space, by=me.name, name=m.name, before=before, after=m.cls_text)
        if m.pk != me.pk:
            Notification.objects.create(
                space=space, member=m, kind=Notification.Kind.SEAT, day=timezone.localdate(), pushed=True,
                text=f"{me.name} 선생님(관리자)이 내 소속을 {m.cls_text}{_josa(m.cls_text, '으로', '로')} 옮겼어요.",
                link=f"/s/{space.token}/school")
        done = f"{m.name} 선생님을 {label}{_josa(label, '으로', '로')} 옮겼어요."
    rest = sorted(Member.objects.in_space(space).filter(group=group).exclude(pk__in=[m.pk for m in picked]), key=_in_group)
    for i, m in enumerate(picked + rest):
        m.order = i
    Member.objects.bulk_update(picked + rest, ["order"])
    return done


def _move_duty(space, what, target, keys):
    """업무분장: 부서 순서(depts, keys = 부서 이름) 또는 부서 안 줄 순서(duties, keys = 그 부서의 줄). 다른 부서 줄이 오면 dept를 바꾼다.
    부서 순서는 Duty.order가 곧 표 순서라 전체를 다시 매긴다."""
    by_dept = {}
    for d in Duty.objects.in_space(space).order_by("order", "pk"):
        by_dept.setdefault(d.dept, []).append(d)
    if what == "depts":
        order = [k for k in keys if k in by_dept] + [k for k in by_dept if k not in keys]
        rows = [d for k in order for d in by_dept[k]]
        done = "부서 순서를 바꿨어요."
    else:
        if target not in by_dept:
            raise ValueError("없는 부서예요. 화면을 새로 고쳐 주세요.")
        mine = {str(d.pk): d for ds in by_dept.values() for d in ds}
        picked = [mine[k] for k in keys if k in mine]
        moved = [d for d in picked if d.dept != target]
        if len(moved) > 1:
            raise ValueError("한 번에 한 줄씩 옮겨 주세요.")
        for d in moved:
            d.dept = target
            Duty.objects.filter(pk=d.pk).update(dept=target)
        ids = {d.pk for d in picked}
        rows = []
        for dept, ds in by_dept.items():
            if dept == target:
                rows += picked + [d for d in ds if d.pk not in ids]
            else:
                rows += [d for d in ds if d.pk not in ids]
        done = f"업무 한 줄을 {target}{_josa(target, '으로', '로')} 옮겼어요." if moved else "순서를 바꿨어요."
    for i, d in enumerate(rows):
        d.order = i
    Duty.objects.bulk_update(rows, ["order"])
    return done


@never_cache
@require_POST
@school_required
@manager_required
@school_write_required
def arrange(request):
    """Q2 순서·소속 바꾸기(관리자 끌어서 놓기·단추). what: groups(소속 카드)·members(소속 안, group=소속)·depts(부서 카드)·
    duties(부서 안, group=부서). 응답은 본문 통째로: 실패해도 서버 상태(= 원래 자리)와 안내가 그려진다."""
    space, what, keys = request.space, request.POST.get("what", ""), _keys(request)
    target = request.POST.get("group", "")
    if keys is None or what not in ("groups", "members", "depts", "duties") or (what == "members" and target not in Member.Group.values):
        return school_body(request, status=400, error="저장하지 못했어요. 화면을 새로 고쳐 다시 해 주세요.")
    try:
        with transaction.atomic():
            Space.objects.select_for_update(no_key=True).filter(pk=space.pk).first()  # 관리자 둘이 동시에 바꿔도 순서가 섞이지 않게
            if what == "groups":
                space.group_order = ",".join(k for k in keys if k in Member.Group.values)
                Space.objects.filter(pk=space.pk).update(group_order=space.group_order)
                done = "소속 순서를 바꿨어요."
            elif what == "members":
                done = _move_member(request, target, keys)
            else:
                done = _move_duty(space, what, target, keys)
    except ValueError as e:  # 안에서 바꾼 것은 모두 되돌린다
        return school_body(request, status=400, error=str(e))
    return school_body(request, toast=done)


@never_cache
@require_POST
@school_required
@manager_required
@school_write_required
def group_color(request):
    """U1 소속 색 바꾸기(관리자). 이미 다른 소속이 쓰던 색이면 그 소속은 이 소속의 옛 색을 받는다(학교 안에서 겹치지 않게)."""
    space, g, color = request.space, request.POST.get("group", ""), request.POST.get("color", "")
    if g not in Member.Group.values or color not in GROUP_PALETTE:
        return school_body(request, status=400, error="저장하지 못했어요. 화면을 새로 고쳐 다시 해 주세요.")
    with transaction.atomic():
        space = Space.objects.select_for_update(no_key=True).get(pk=space.pk)  # 관리자 둘이 동시에 바꿔도 겹치지 않게
        colors = space.group_colors
        other = next((k for k, v in colors.items() if v == color), None)  # 저장값이 어긋나 아무도 안 쓰는 색이면 맞바꿀 상대 없음
        if other:
            colors[other] = colors[g]
        colors[g] = color
        Space.objects.filter(pk=space.pk).update(group_color=",".join(f"{k}={v}" for k, v in colors.items()))
    request.space.refresh_from_db(fields=["group_color"])
    label, name = Member.Group(g).label, GROUP_PALETTE[color][1]
    return school_body(request, toast=f"{label} 색을 {name}{_josa(name, '으로', '로')} 바꿨어요.")


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
    """관리자 지정·해제. 계정 회원(구글·아이디)만 지정, 마지막 관리자는 해제 거부. 직위 글자는 보지 않는다."""
    m = get_in_space_or_404(Member, request, key=key)
    on, error = request.POST.get("on") == "1", ""
    if on != m.is_manager:
        if on and (m.user_id is None or is_guest(m.user)):
            error = "이름만 들어온 선생님은 관리자가 될 수 없어요. 구글이나 아이디로 이으면 돼요."
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


@never_cache
@require_POST
@school_required
@manager_required
def temp_password(request, key):
    """임시 비밀번호 발급(P1, 리드 결정): 메일이 없어 학교 관리자가 명부에서 준다. 아이디 회원만, 관리자 대상은 거부.
    화면에 한 번만 보이고 저장하지 않는다. 받은 회원은 다음 로그인 때 바꾸기를 강제하고(PasswordChangeRequired), 🔔로 알린다. 그 회원의 기기 토큰을 모두 지우고(세션은 비밀번호 해시가 바뀌어 끊긴다) 이력에 남긴다.
    학교 플랜이 끝나도 막지 않는다(학교 자료 쓰기가 아니라 계정 되찾기)."""
    m = get_in_space_or_404(Member, request, key=key)
    ctx = {"space": request.space, "m": m}
    if m.is_manager or m.user_id is None or not is_id_member(m.user):
        ctx["error"] = "아이디로 가입한 선생님(관리자 제외)만 임시 비밀번호를 줄 수 있어요."
    else:
        ctx.update(username=m.user.username, temp="".join(secrets.choice(TEMP_CHARS) for _ in range(10)))
        with transaction.atomic():
            m.user.set_password(ctx["temp"])
            m.user.save(update_fields=["password"])
            DeviceToken.objects.filter(user=m.user).delete()
            PasswordChangeRequired.objects.get_or_create(user=m.user)  # 다음 로그인 뒤 바꾸기 전에는 바꾸기 화면만
            Notification.objects.create(  # 본인이 모르는 발급을 알아챌 수 있게(🔔만)
                space=request.space, member=m, kind=Notification.Kind.SEAT, day=timezone.localdate(), pushed=True,
                text=f"{request.member.name} 선생님(관리자)이 내 임시 비밀번호를 발급했어요. 요청하지 않았다면 관리자에게 알려 주세요.",
                link=f"/s/{request.space.token}/settings")
            MemberLog.objects.create(space=request.space, by=request.member.name, name=m.name,
                                     before="비밀번호", after="임시 비밀번호 발급")
    tpl = "spaces/_temp_password.html" if request.headers.get("HX-Request") else "spaces/temp_password.html"
    return render(request, tpl, ctx, status=400 if "error" in ctx else 200)


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
                Space.objects.select_for_update(no_key=True).filter(pk=space.pk).first()  # 관리자 둘이 동시에 바꿔도 한 표만 남게
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
