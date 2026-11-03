"""C4 받는 사람 줄: 동학년 명부(Space의 Member)로 자동 생성 + 명부 밖 사람이 링크로 줄 고르기·이름 쓰기.

prototype TR.audRows 이식. 동학년판에는 그룹이 학년 하나뿐이라 두 가지만 쓴다.
- "class"(hrOnly '담임만 · 반별 1칸'): 반 번호가 있는 회원만(전담 빠짐 — 전담은 '명부 밖'처럼 링크로 고르거나 이름을 쓴다).
- "people"('사람마다 한 줄', 직위·성명): 전담 포함 전원.
명부 밖(prototype ext, design '명부 밖 선생님도 링크로 받기'): allow_outside일 때만, 마감 전까지. 받는 화면은 C5.

H4 학교 공간: 범위(audience)를 골라 줄을 만든다(prototype spec·TR.audRows·TR.audText).
- all: 전 교직원 / groups: 고른 그룹(hr면 담임만·반별 1칸) / pergroup: 그룹마다 1칸(그룹 누구나) /
  dept: 업무분장(Duty) 담당자 / people: 고른 사람. 줄 순서는 학년·반, 그다음 교무실·행정실·급식소·기타.
동학년은 범위가 없다({}): 지금처럼 반별 명부.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404

from spaces.models import Duty, Member
from spaces.school import _order, _person, split_people
from spaces.views import _clean_name

from .models import Collection, Row

OUTSIDE_UNIT = "명부 밖"
OUTSIDE_MAX = 60  # 명부 밖 줄 상한(링크가 새도 줄이 끝없이 늘지 않게)
GRADES = [g for g in Member.Group.values if g[1:].isdigit()]
GROUPS = dict(Member.Group.choices)
HR_HEAD, PEOPLE_HEAD = ["학년반", "담임"], ["직위", "성명"]  # prototype lockCols


class OutsideClosed(Exception):
    """명부 밖 줄을 만들 수 없음(허용 안 함·마감·상한). 문구는 받는 화면(C5)이 정한다."""


def _class_no(cls):
    tail = cls.split("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _members(space):
    """줄 순서로 정렬한 명부. 학교 공간은 소속 순(학년·반, 전담은 뒤, 교무실·행정실·급식소·기타), 같으면 학교 화면 순서."""
    members = list(Member.objects.in_space(space).order_by("joined_at", "pk"))
    if space.is_school:
        rank = {g: i for i, g in enumerate(Member.Group.values)}
        members.sort(key=lambda m: (rank.get(m.group, len(rank)), _order(m)))
    else:
        members.sort(key=lambda m: (m.cls.partition("-")[0], _class_no(m.cls) is None, _class_no(m.cls) or 0))
    return members


def _lines(members, mode):
    out = []
    for m in members:
        no = _class_no(m.cls)
        if mode == "class":
            if no is not None:
                out.append({"unit": m.cls, "who": m.name, "member": m})
        else:
            out.append({"unit": m.cls_text, "who": m.name, "member": m})
    return out


def roster_rows(space, mode):
    """[{"unit", "who", "member"}] — 학년(학교 공간), 반 순서(전담은 뒤), 같은 반이면 들어온 순서."""
    return _lines(_members(space), mode)


def read_audience(space, data):
    """폼 값 → 범위. 고른 값은 이 공간 명부·업무분장과 맞은 것만 남긴다(다른 공간 값·이상한 입력은 DB에 묻지 않고 빠진다)."""
    if not space.is_school:
        return {}
    mode = data.get("aud", "")
    if mode == "all":
        return {"mode": "all"}
    if mode == "pergroup":
        return {"mode": mode, "groups": [g for g in GROUPS if g in data.getlist("aud_pgroups")]}
    if mode == "dept":
        known = set(Duty.objects.in_space(space).values_list("dept", flat=True))
        return {"mode": mode, "depts": list(dict.fromkeys(d for d in data.getlist("aud_depts") if d in known))}
    if mode == "people":
        known = set(Member.objects.in_space(space).values_list("key", flat=True))
        return {"mode": mode, "people": list(dict.fromkeys(k for k in data.getlist("aud_people") if k in known))}
    if mode == "groups":
        return {"mode": mode, "groups": [g for g in GROUPS if g in data.getlist("aud_groups")], "hr": data.get("aud_hr") == "on"}
    return {"mode": "groups", "groups": GRADES, "hr": True}  # prototype 첫 화면: 1~6학년 담임


def _dept_rows(space, depts):
    """부서 담당자마다 한 줄(부서는 표에 나온 순서, 한 사람은 한 줄). 담당은 이름 글자라 명부와 짝짓는다:
    명부에 없거나 같은 이름이 둘이면 회원 없는 줄(이름이 같은 사람의 '낼 것'에 뜨는 명부 밖 줄과 같음)."""
    by_name = {}
    for m in Member.objects.in_space(space):
        by_name.setdefault(_person(m.name), []).append(m)
    by_dept, seen = {}, set()
    for d in Duty.objects.in_space(space).filter(dept__in=depts).order_by("order", "pk"):
        people = by_dept.setdefault(d.dept, [])
        for n in split_people(d.who, by_name):
            if n not in seen:
                seen.add(n)
                found = by_name.get(n, [])
                m = found[0] if len(found) == 1 else None
                people.append({"unit": d.dept, "who": m.name if m else n, "member": m})
    return [r for people in by_dept.values() for r in people]


def audience_rows(space, aud):
    """범위 → 줄들. 그룹 줄만 "group"이 있다."""
    mode = aud.get("mode")
    if mode is None:
        return roster_rows(space, "class")
    if mode == "pergroup":
        return [{"unit": GROUPS[g], "who": "", "member": None, "group": g} for g in aud["groups"]]
    if mode == "dept":
        return _dept_rows(space, aud["depts"])
    members = _members(space)
    if mode == "people":
        members = [m for m in members if m.key in aud["people"]]
    elif mode == "groups":
        members = [m for m in members if m.group in aud["groups"]]
    return _lines(members, "class" if aud.get("hr") else "people")


def audience_head(aud):
    mode = aud.get("mode")
    if mode is None or aud.get("hr"):
        return list(HR_HEAD)
    return {"pergroup": ["그룹", "작성"], "dept": ["부서", "담당"]}.get(mode, list(PEOPLE_HEAD))


def audience_text(aud, n):
    """prototype TR.audText: 받는 곳 설명 한 줄."""
    mode = aud.get("mode")
    if mode == "all":
        return f"전 교직원 {n}명 · 사람별 1칸"
    if mode == "people":
        return f"개인 {n}명 · 사람별 1칸"
    if mode == "dept":
        return f"부서 {len(aud['depts'])}곳 담당 {n}명 · 사람별 1칸"
    groups = aud.get("groups", [])
    names = [GROUPS[g] for g in groups]
    if set(GRADES) <= set(groups):
        names = ["1~6학년"] + [GROUPS[g] for g in groups if g not in GRADES]
    gl = "전 그룹" if len(groups) == len(GROUPS) else "·".join(names[:3]) + (f" 외 {len(names) - 3}" if len(names) > 3 else "")
    if mode == "pergroup":
        return f"그룹 {n}곳({gl}) · 그룹별 1칸"
    return f"{gl} 담임 {n}명 · 반별 1칸" if aud.get("hr") else f"{gl} {n}명 · 사람별 1칸"


def audience_where(aud):
    """쪽지 문구의 '…만 채워 주세요'."""
    if aud.get("mode") == "pergroup":
        return "우리 그룹 칸"
    return "우리 반 칸" if aud.get("mode") is None or aud.get("hr") else "내 칸"


def audience_form(space, aud):
    """고르는 화면 값(_aud_school.html). 고른 것은 다시 그릴 때 그대로 둔다."""
    members = _members(space)
    picked = set(aud.get("people", []))
    groups, mode = aud.get("groups", []), aud.get("mode")
    n = {g: sum(m.group == g for m in members) for g in GROUPS}
    names, known = {_person(m.name) for m in members}, {}
    for d in Duty.objects.in_space(space).order_by("order", "pk"):
        known.setdefault(d.dept, set()).update(split_people(d.who, names))
    return {"aud_mode": mode, "aud_hr": aud.get("hr", True),
            "aud_groups": [{"id": g, "label": label, "n": n[g], "on": g in groups and mode == "groups", "pon": g in groups and mode == "pergroup"}
                           for g, label in GROUPS.items()],
            "aud_depts": [{"name": d, "n": len(people), "on": d in aud.get("depts", [])} for d, people in known.items()],
            "aud_people": [{"label": label, "members": [{"key": m.key, "name": m.name, "pos": m.position or m.cls_text, "on": m.key in picked}
                                                        for m in members if m.group == g]}
                           for g, label in GROUPS.items() if n[g]]}


def unmatched_count(aud, rows):
    """부서 범위에서 명부와 짝짓지 못한 담당(0명·동명이인) 수. 이 줄은 누구의 '낼 것'에도 뜨지 않는다."""
    return sum(r["member"] is None for r in rows) if aud.get("mode") == "dept" else 0


def stored_audience(aud, rows):
    """저장할 범위: 다시 걷기에 쓸 값 + 만들 때의 설명·짝 못 지은 수. 동학년은 비어 있다."""
    return {**aud, "text": audience_text(aud, len(rows)), "unmatched": unmatched_count(aud, rows)} if aud else {}


def make_rows(collection, rows):
    Row.objects.bulk_create([Row(collection=collection, pos=i, unit=r["unit"], who=r["who"], member=r["member"], group=r.get("group", ""))
                             for i, r in enumerate(rows)])


def pick_row(collection, key):
    """링크로 들어온 사람이 고른 줄. 이 모으기의 줄만(다른 모으기 줄 키면 404). 대신 내기 허용이라 누구나 고를 수 있다."""
    return get_object_or_404(Row, collection=collection, key=key)


def add_outside_row(collection, name):
    """명부 밖 사람이 이름을 써서 새 줄을 만든다. 같은 이름의 명부 밖 줄이 있으면 그 줄을 돌려준다."""
    name = _clean_name(name)
    if not name:
        raise ValueError("이름이 비었습니다.")
    with transaction.atomic():
        c = Collection.objects.select_for_update().get(pk=collection.pk)  # 동시에 같은 이름·순서 겹침 막기
        if not c.allow_outside or c.closed_at:
            raise OutsideClosed
        same = c.rows.filter(member__isnull=True, unit=OUTSIDE_UNIT, who=name).first()
        if same:
            return same
        if c.rows.filter(member__isnull=True, unit=OUTSIDE_UNIT).count() >= OUTSIDE_MAX:
            raise OutsideClosed
        last = c.rows.order_by("-pos").values_list("pos", flat=True).first()
        return Row.objects.create(collection=c, pos=0 if last is None else last + 1, unit=OUTSIDE_UNIT, who=name)
