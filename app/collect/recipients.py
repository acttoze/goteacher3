"""C4 받는 사람 줄: 동학년 명부(Space의 Member)로 자동 생성 + 명부 밖 사람이 링크로 줄 고르기·이름 쓰기.

prototype TR.audRows 이식. 동학년판에는 그룹이 학년 하나뿐이라 두 가지만 쓴다.
- "class"(hrOnly '담임만 · 반별 1칸'): 반 번호가 있는 회원만(전담 빠짐 — 전담은 '명부 밖'처럼 링크로 고르거나 이름을 쓴다).
- "people"('사람마다 한 줄', 직위·성명): 전담 포함 전원.
명부 밖(prototype ext, design '명부 밖 선생님도 링크로 받기'): allow_outside일 때만, 마감 전까지. 받는 화면은 C5.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404

from spaces.models import Member
from spaces.views import _clean_name

from .models import Collection, Row

OUTSIDE_UNIT = "명부 밖"
OUTSIDE_MAX = 60  # 명부 밖 줄 상한(링크가 새도 줄이 끝없이 늘지 않게)


class OutsideClosed(Exception):
    """명부 밖 줄을 만들 수 없음(허용 안 함·마감·상한). 문구는 받는 화면(C5)이 정한다."""


def _class_no(cls):
    tail = cls.split("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def roster_rows(space, mode):
    """[{"unit", "who", "member"}] — 반 순서(전담은 뒤), 같은 반이면 들어온 순서."""
    members = list(Member.objects.in_space(space).order_by("joined_at", "pk"))
    members.sort(key=lambda m: (_class_no(m.cls) is None, _class_no(m.cls) or 0))
    out = []
    for m in members:
        no = _class_no(m.cls)
        if mode == "class":
            if no is not None:
                out.append({"unit": m.cls, "who": m.name, "member": m})
        else:
            pos = f"{no}반" if no is not None else m.cls.split("-", 1)[-1]
            out.append({"unit": f"{space.grade}학년 {pos}", "who": m.name, "member": m})
    return out


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
