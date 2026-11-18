"""W7 명단으로 한 번에 등록(학교 관리자): 교직원 명단 표 붙여넣기 → 미리보기(소속·반 해석, 줄마다 상태) → 주인 없는 새 자리 + 입장 코드
→ A4 절취 쪽지 인쇄.

- 전화·생년월일·주민번호·메일로 보이는 열은 브라우저(bulk_paste.js)가 지우고 보낸다. 서버도 이름·소속·반·직책 4개 값만 읽고 나머지 칸은 버린다.
  붙여넣은 원문은 저장하지 않고, 미리보기에도 4개 값만 그린다.
- 코드는 이 화면에서 만든 새 자리에만 준다(이력이 있을 수 있는 기존 빈 자리는 명부에서 하나씩, W4).
- 코드 원문은 만들기 응답에 한 번만 나온다(never_cache: no-store). 세션·DB·로그에 남기지 않는다.
"""
import re

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from collect.paste import FILLER, TOTAL, PasteError, tsv_lines

from . import entry
from .gates import manager_required, no_org, school_required
from .models import Member, MemberLog, Space
from .school import sort_members
from .views import GROUP_ROLES, SCHOOL_SEATS, _clean_name, _cls_label, _grade_choices, _space_ctx

MAX_ROWS = 150  # 한 번에 만드는 줄
MAX_TEXT = 60_000
SLIPS = 8  # A4 한 장의 쪽지 칸
HEAD_LINES = 5
CLOSED = "지금은 명단으로 등록할 수 없어요. 학교 플랜 기간이 끝났거나, 예전 고티처와 함께 쓰는 중이에요."
EMPTY = "명단 표를 붙여넣어 주세요. 엑셀·한글에서 이름·소속·반(또는 직책) 칸을 드래그해 복사하면 돼요."
TOO_MANY = f"한 번에 {MAX_ROWS}명까지 넣을 수 있어요. 나눠서 붙여넣어 주세요."
STUDENT = "학생 명단은 받지 않아요. 교직원 명단만 붙여넣어 주세요."
FIX_FIRST = "「고칠 것」 줄의 소속·반을 고르거나 빼 주세요."

NAME_H = re.compile(r"^(이름|성명|성함|교사명|교원명|교직원명|직원명)$")
GROUP_H = re.compile(r"^(소속|부서|부서명|소속부서|학년|학년부|근무부서)$")
CLS_H = re.compile(r"^(반|학급|학년반|담임|담임학급|담당학급)$")
ROLE_H = re.compile(r"^(직책|직위|직급|보직|직명)$")
BOTH_H = re.compile(r"^(반|학급)?[/·,]?(직책|직위)[/·,]?(반|학급)?$|^반또는직책$")
STUDENT_H = re.compile(r"학번|학생|원아|보호자|학부모")
KOR = {"일": "1", "이": "2", "삼": "3", "사": "4", "오": "5", "육": "6"}
GRADE_WORD = re.compile(r"^([1-6일이삼사오육])학년(?:부)?(.*)$")
DASHED = re.compile(r"^([1-6])-(.+)$")
OTHER = {"교무실": "office", "교무": "office", "행정실": "admin", "행정": "admin", "급식소": "meal", "급식실": "meal", "급식": "meal",
         "조리실": "meal", "기타": "support", "지원": "support", "기타/지원": "support", "기타지원": "support"}
# bulk_paste.js와 같은 판정(서버에서 한 번 더): 공백·괄호 설명을 걷어 낸 값으로 본다
PRIVATE = [re.compile(x) for x in (r"^0\d{1,2}[-.)]?\d{3,4}[-.]?\d{4}$", r"^\d{6}-?[1-8][\d*]{6}$", r"\S+@\S+\.\S+",
                                    r"^(19|20)?\d{2}[.\-/년]\d{1,2}[.\-/월]\d{1,2}일?\.?$", r"^(19|20)\d{6}$", r"^\d{6}$")]
STUDENT_AT = 3  # 같은 반 담임으로 보이는 줄이 이만큼이면 학생 명단으로 본다
STUDENT_LIKE = "학생 명단은 받지 않아요. 교직원 명단인지 확인해 주세요."
STATUS = {"fix": "고칠 것", "exists": "이미 있음 · 건너뜀", "dup": "동명이인 주의", "ok": "만들 수 있음"}


def _bare(s):
    return re.sub(r"\s+", "", s or "")


def _person(raw):
    """이름: 한글 조각만 띄어 쓴 표('홍 길 동')는 붙인다."""
    name = _clean_name(raw)
    parts = name.split()
    return "".join(parts) if len(parts) > 1 and all(re.fullmatch(r"[가-힣]+", p) for p in parts) else name


def _columns(line):
    found = {}
    for c, cell in enumerate(line):
        bare = _bare(cell)
        for key, pat in (("name", NAME_H), ("group", GROUP_H), ("cls", CLS_H), ("role", ROLE_H)):
            if pat.match(bare):
                found.setdefault(key, c)
        if "cls" not in found and "role" not in found and BOTH_H.match(bare):
            found["cls"] = found["role"] = c
    return found


def read_list(text):
    """TSV → [{name, group, cls, role}] (적힌 글자 그대로). 4개 칸 밖은 버린다. 문제가 있으면 PasteError(붙여넣은 값은 담지 않는다)."""
    if len(text) > MAX_TEXT:
        raise PasteError(TOO_MANY)
    lines = tsv_lines(text)
    if not lines:
        raise PasteError(EMPTY)
    if any(len(c) <= 12 and STUDENT_H.search(c) for r in lines[:HEAD_LINES] for c in r):
        raise PasteError(STUDENT)
    head = next((i for i, r in enumerate(lines[:HEAD_LINES]) if {"name", "group"} <= _columns(r).keys()), None)
    if head is not None:
        cols, data = _columns(lines[head]), lines[head + 1:]
    elif max(len(r) for r in lines) >= 2:
        k = 1 if all(r[0].isdigit() for r in lines) else 0  # 맨 앞 번호 칸
        cols, data = {"name": k, "group": k + 1, "cls": k + 2, "role": k + 2}, lines  # 머리글 없는 표: 이름 | 소속 | 반 또는 직책
    else:
        raise PasteError("칸 구분이 없어요. 엑셀이나 한글에서 표를 드래그해 복사해 붙여넣어 주세요.")
    cols.setdefault("role", cols.get("cls"))
    cols.setdefault("cls", cols.get("role"))
    rows = []
    for r in data:
        cell = {k: (r[c] if c is not None and c < len(r) else "") for k, c in cols.items()}
        if any(TOTAL.match(_bare(v)) for v in r) or all(FILLER.match(v) for v in r if v) or {"name", "group"} <= _columns(r).keys():
            continue  # 합계·채움 줄, 쪽마다 다시 나온 머리글
        if not _person(cell["name"]):
            continue
        rows.append(cell)
    if not rows:
        raise PasteError("이름이 적힌 줄을 찾지 못했어요. 이름·소속 칸이 있는지 확인해 주세요.")
    if len(rows) > MAX_ROWS:
        raise PasteError(TOO_MANY)
    return rows


def read_group(text):
    """소속 글자 → (소속 값, 남은 글자). '1학년'·'1학년부'·'사학년'·'2학년 1반'·'3-2'·'교무실'… 못 읽으면 ('', '')."""
    bare = _bare(text)
    if bare in Member.Group.values:
        return bare, ""
    m = GRADE_WORD.match(bare)
    if m:
        return "g" + KOR.get(m[1], m[1]), m[2]
    m = DASHED.match(bare)
    if m:
        return "g" + m[1], bare
    return OTHER.get(bare, ""), ""


def read_cls(group, text, choices):
    """반 글자 → 반 값('4-2', '4-전담'). '2반'·'2'·'4-2'·'4학년 2반'·'교과전담'. 이 학년 칸에 없으면 ''."""
    n, bare = group[1:], _bare(text)
    bare = re.sub(rf"^{n}학년", "", bare)
    m = DASHED.match(bare)
    if m:
        if m[1] != n:
            return ""
        bare = m[2]
    tail = "전담" if "전담" in bare else bare.removesuffix("반")
    tail = tail.lstrip("0") or tail if tail.isdigit() else tail
    cls = f"{n}-{tail}"
    return cls if cls in choices else ""


def _private(v):
    """전화·주민번호·메일·생년월일로 보이는 값. '010 - 1234 - 5678'·'010-1234-5678(개인)'도."""
    bare = re.sub(r"\s+", "", re.sub(r"[(（][^)）]*[)）]?", "", v or ""))
    return bool(bare) and any(p.search(bare) for p in PRIVATE)


def _resolve(school, raw, choices):
    """적힌 4개 값 → 화면·저장에 쓰는 줄. choices: {학년 소속: 반 값들}(한 요청 안에서 나이스를 한 번씩만)."""
    raw, bad = dict(raw), False
    for k in ("name", "group", "cls", "role"):
        v = raw.get(k, "")
        if _private(v) or k == "role" and len(re.findall(r"\d", v)) >= 7:  # 값은 지우고 그 줄은 고칠 것으로
            raw[k], bad = "", True
    group, rest = read_group(raw.get("group", ""))
    row = {"bad": bad, "name": _person(raw.get("name", "")), "group": group, "cls": "", "role": "", "off": raw.get("off") == "1",
           "raw_group": "" if group else _clean_name(raw.get("group")), "raw_cls": _clean_name(raw.get("cls") or rest)}
    if group[1:].isdigit():
        if group not in choices:
            choices[group] = _grade_choices(school, int(group[1:]))
        row["cls"] = read_cls(group, raw.get("cls") or rest, choices[group])
        row["classes"] = [(c, _cls_label(c)) for c in choices[group]]
        row["role"] = _clean_name(raw.get("role")) if raw.get("role") != raw.get("cls") else ""  # 따로 적힌 직책(담임 세기에서 뺀다)
    elif group:
        row["cls"], row["role"] = Member.Group(group).label, _clean_name(raw.get("role"))
        row["roles"] = list(dict.fromkeys([*GROUP_ROLES[group], *([row["role"]] if row["role"] else [])]))
    return row


def _check(space, rows):
    """줄마다 상태: fix(소속·반 못 읽음) · exists(같은 이름·소속·반 자리가 있거나 목록에 두 번) · dup(같은 이름이 다른 소속에) · ok."""
    have, places = set(), {}
    for name, group, cls in Member.objects.in_space(space).values_list("name", "group", "cls"):
        have.add((name, group, cls))
        places.setdefault(name, set()).add((group, cls))
    for r in rows:
        if not r["off"] and r["group"] and r["cls"]:
            places.setdefault(r["name"], set()).add((r["group"], r["cls"]))
    homeroom = {}  # 같은 반 담임으로 보이는 줄(전담·직책 있는 줄은 세지 않는다)
    for r in rows:
        if not r["off"] and r["group"][1:].isdigit() and r["cls"].partition("-")[2].isdigit() and not r["role"]:
            homeroom.setdefault(r["cls"], []).append(r)
    for same in homeroom.values():
        for r in same:
            r["student"] = len(same) >= STUDENT_AT
    seen = set()
    for r in rows:
        key = (r["name"], r["group"], r["cls"])
        if r.get("bad") or r.get("student") or not r["group"] or not r["cls"]:
            r["status"] = "fix"
        elif key in have or key in seen:
            r["status"], r["twice"] = "exists", key not in have
        else:
            r["status"] = "dup" if len(places.get(r["name"], ())) > 1 else "ok"
        if not r["off"]:
            seen.add(key)
        r["label"] = STATUS[r["status"]]
    return rows


def _summary(space, rows):
    live = [r for r in rows if not r["off"]]
    n = {k: sum(1 for r in live if r["status"] == k) for k in STATUS}
    new = n["ok"] + n["dup"]
    room = SCHOOL_SEATS - Member.objects.in_space(space).count()
    return {"student": any(r.get("student") for r in live), "n": n, "n_new": new, "n_off": len(rows) - len(live), "seats": SCHOOL_SEATS, "room": room, "full": new > room,
            "ready": new and not n["fix"] and new <= room}


def _form_rows(post, school):
    try:
        n = int(post.get("n", "0"))
    except ValueError:
        n = 0
    if n > MAX_ROWS:
        raise PasteError(TOO_MANY)
    choices, rows = {}, []
    for i in range(max(n, 0)):
        raw = {k: post.get(f"r{i}-{k}", "") for k in ("name", "group", "cls", "role", "off")}
        if _person(raw["name"]):
            rows.append(_resolve(school, raw, choices))
    return rows


def _closed(request):
    space = request.space
    return not space.school.writable or space.legacy_readonly


def _page(request, status=200, **ctx):
    return render(request, "spaces/bulk.html", _space_ctx(request, page="school", **ctx), status=status)


@never_cache
@require_POST
@school_required
@no_org
@manager_required
def preview(request):
    """미리보기(htmx). 처음엔 붙여넣은 표(paste), 그 뒤엔 미리보기의 줄 값(r{i}-…)을 다시 읽는다. 저장하지 않는다."""
    if _closed(request):
        return HttpResponseForbidden(CLOSED)
    space, ctx = request.space, {"space": request.space}
    try:
        if "paste" in request.POST:
            choices = {}
            rows = [_resolve(space.school, r, choices) for r in read_list(request.POST["paste"])]
            rows = _check(space, rows)
            rows.sort(key=lambda r: r["status"] != "fix")  # 고칠 것 먼저(그 뒤엔 보낸 순서 그대로)
        else:
            rows = _check(space, _form_rows(request.POST, space.school))
    except PasteError as e:
        ctx["error"] = str(e)
    else:
        ctx.update(lines=rows, **_summary(space, rows))
    return render(request, "spaces/_bulk_out.html", ctx)


def _make(request, rows):
    """한 트랜잭션: 공간 행을 잠그고 다시 확인 → 새 자리 → 자리마다 입장 코드 → 이력 한 줄. 거절은 ValueError(안내 문구)."""
    me, made = request.member, []
    with transaction.atomic():
        space = Space.objects.select_for_update(no_key=True).get(pk=request.space.pk)  # 두 번 누름·관리자 둘·명부 입장과 직렬화
        if not Member.objects.in_space(space).filter(pk=me.pk, is_manager=True).exists():
            raise ValueError("학교 관리자만 명단으로 등록할 수 있어요.")
        rows = _check(space, rows)
        live = [r for r in rows if not r["off"]]
        if any(r["status"] == "fix" for r in live):
            raise ValueError(FIX_FIRST)
        new = [r for r in live if r["status"] in ("ok", "dup")]
        have = Member.objects.in_space(space).count()
        if have + len(new) > SCHOOL_SEATS:
            raise ValueError(f"학교 공간에는 {SCHOOL_SEATS}명까지 있을 수 있어요. 지금 {have}명이라 {SCHOOL_SEATS - have}명까지 더 만들 수 있어요.")
        for r in new:
            m = Member.objects.create(space=space, name=r["name"], cls=r["cls"], group=r["group"],
                                      position=_cls_label(r["cls"]) if r["group"][1:].isdigit() else r["role"])
            code, until = entry.issue(m, request.user)
            made.append((m, code, until))
        if made:
            MemberLog.objects.create(space=space, by=me.name, name=f"일괄 등록 {len(made)}명", before="명단 붙여넣기", after="입장 코드 발급")
    return made


def _slips(space, made):
    codes = {m.pk: (code, until) for m, code, until in made}
    ordered = sort_members(space, [m for m, _, _ in made])
    slips = [{"m": m, "code": codes[m.pk][0], "until": codes[m.pk][1]} for m in ordered]
    return [slips[i:i + SLIPS] for i in range(0, len(slips), SLIPS)]


@never_cache
@require_http_methods(["GET", "POST"])
@school_required
@no_org
@manager_required
def page(request):
    """붙여넣기 화면(GET)과 만들기(POST). 만들기 응답이 코드를 한 번만 보여 주는 쪽지 화면이다."""
    if _closed(request):
        return HttpResponseForbidden(CLOSED)
    if request.method == "GET":
        return _page(request)
    space, rows = request.space, []
    try:
        rows = _form_rows(request.POST, space.school)
        if not rows:
            raise PasteError(EMPTY)
        made = _make(request, rows)
    except (PasteError, ValueError) as e:
        return _page(request, status=400, error=str(e), lines=_check(space, rows), **_summary(space, rows))
    if not made:
        return _page(request, nothing=True)
    end = made[0][2]
    url = f"{settings.SITE_URL}/enter"
    msg = (f"[고티처] {space.school.name} 학교 공간 입장 안내예요.\n"
           "관리자에게 받은 쪽지의 입장 코드로 내 자리에 들어와요.\n\n"
           f"1. {url} 주소를 열어요.\n2. 쪽지의 입장 코드를 넣어요.\n3. 구글이나 아이디로 계속해요. 처음이면 가입하면 돼요.\n\n"
           f"코드는 {end.month}월 {end.day}일까지 한 번만 쓸 수 있어요. 쪽지를 잃어버렸으면 관리자에게 새 코드를 받아 주세요.")
    return render(request, "spaces/bulk_done.html", {"space": space, "sheets": _slips(space, made), "n": len(made),
                                                      "enter_url": url, "msg": msg})
