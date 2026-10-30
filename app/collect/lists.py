"""D6 모으기 목록(내가 걷는 것·내가 낼 것·지난 것)과 다시 걷기. 모습은 design/collect.html.

- 내가 걷는 것: 내가 만든 것 중 잠기지 않고 마감 전(design status open·done).
- 지난 것: 내가 만든 것 중 잠갔거나 마감이 지난 것(design status past).
- 내가 낼 것: 남이 만든 잠기지 않은 모으기에서 내 줄이 아직 안 낸 것(prototype updateDots: !c.mine && mineRow && !done).
  내 줄 = Row.member가 나, 또는 명부 밖 줄(member 없음) 이름이 내 이름(prototype TR.rowMine).
- 다시 걷기: prototype·design에 없음. 리드 지시대로 칸·설정만 옮기고 줄은 지금 명부로 다시 만든다.
낸 것 판정은 받는 사람 화면과 같은 submit._units를 쓴다. 조회는 모두 in_space()를 거친다.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from spaces.models import Member, get_in_space_or_404
from spaces.views import PEOPLE_COLORS, _space_ctx

from .models import Collection, Field, Row
from .presets import PRESETS
from .recipients import roster_rows
from .submit import _head, _units
from .views import _board, _due_text, _owner

TABS = [("sent", "내가 걷는 것"), ("todo", "내가 낼 것"), ("past", "지난 것")]
NOUN = {"sent": "모으기", "todo": "낼 것", "past": "지난 모으기"}


def _past(c, now):
    return bool(c.closed_at or (c.due and c.due <= now))


def _urgent(c, now):
    """마감이 오늘 안이거나 지남(design .due.urgent)."""
    return bool(c.due and timezone.localtime(c.due).date() <= timezone.localdate(now))


def _mine_q(me):
    return Q(member=me) | Q(member__isnull=True, who=me.name)


def _colors(space):
    pks = Member.objects.in_space(space).order_by("joined_at", "pk").values_list("pk", flat=True)
    return {pk: PEOPLE_COLORS[i % len(PEOPLE_COLORS)] for i, pk in enumerate(pks)}


def _sent_item(c, now, colors):
    h = _head(c)
    done = [u["row"] for u in h["units"] if u["done"]]
    return {"c": c, "src": c.get_source_display(), "made": timezone.localtime(c.created_at),
            "n_done": h["n_done"], "n_total": h["n_total"], "pct": h["pct"], "per": h["per"],
            "all": h["n_total"] > 0 and h["n_done"] == h["n_total"], "due": _due_text(c), "urgent": _urgent(c, now),
            "done": [(r, *colors.get(r.member_id, ("var(--ink)", False))) for r in done],
            "done_names": ", ".join(f"{r.unit} {r.who}".strip() for r in done), "find": c.title}


def _todo_items(request, now):
    me = request.member
    cs = (Collection.objects.in_space(request.space).select_related("created_by", "space__school")
          .exclude(created_by=me).filter(closed_at__isnull=True)
          .filter(pk__in=Row.objects.in_space(request.space).filter(_mine_q(me)).values("collection")))
    out = []
    for c in cs:
        mine = [u for u in _units(c) if u["row"].member_id == me.pk
                or (u["row"].member_id is None and u["row"].who == me.name)]
        left = [u for u in mine if not u["done"]]
        if not left:
            continue
        by = c.created_by
        out.append({"c": c, "row": left[0]["row"], "from": f"{by.name} · {by.cls}" if by else "",
                    "src": c.get_source_display(), "due": _due_text(c), "urgent": _urgent(c, now),
                    "find": f"{c.title} {by.name if by else ''}"})
    far = now + timedelta(days=36500)
    out.sort(key=lambda x: (x["c"].due or far, x["c"].created_at))
    return out


def _ctx(request, error=None):
    now = timezone.now()
    tab = request.GET.get("tab", "sent")
    tab = tab if tab in NOUN else "sent"
    q = request.GET.get("q", "").strip()[:100]
    colors = _colors(request.space)
    mine = (Collection.objects.in_space(request.space).filter(created_by=request.member)
            .select_related("space__school", "created_by").order_by("-created_at"))
    sets = {"sent": [], "past": [], "todo": _todo_items(request, now)}
    for c in mine:
        sets["past" if _past(c, now) else "sent"].append(_sent_item(c, now, colors))
    shown = {k: [x for x in v if q.lower() in x["find"].lower()] for k, v in sets.items()}
    open_ = [x for x in sets["sent"] if not x["all"]]
    side = _board(open_[0]["c"]) if open_ else None
    return _space_ctx(request, page="list", tab=tab, q=q, items=shown[tab], noun=NOUN[tab], error=error,
                      tabs=[(k, label, len(shown[k])) for k, label in TABS], n_open=len(open_),
                      n_todo=len(sets["todo"]), first_todo=sets["todo"][0] if sets["todo"] else None,
                      side=side, side_c=open_[0] if open_ else None,
                      all_done=[x for x in sets["sent"] if x["all"]])


@never_cache  # 공용 PC: 로그아웃 뒤 '뒤로 가기'로 목록이 보이지 않게
@require_GET
def index(request):
    return render(request, "collect/list.html", _ctx(request))


def _roster_mode(c):
    """원본이 명부를 어떻게 썼나: 양식은 그 양식의 규칙(연수 이수 = 사람마다), 나머지는 반별(C2·C3와 같음)."""
    for spec in PRESETS.values():
        if c.source == Collection.Source.PRESET and c.unit_head == spec["unit_head"]:
            return spec["roster"]
    return "class"


@require_POST
def again(request, key):
    """다시 걷기: 칸·설정을 옮긴 새 모으기. 제출 값·마감·잠금은 비우고, 줄은 지금 동학년 명부로 만든다."""
    src = get_in_space_or_404(Collection, request, key=key)
    if not _owner(request, src):
        return HttpResponseForbidden("다시 걷기는 만든 선생님만 할 수 있어요.")
    mode = _roster_mode(src)
    rows = roster_rows(request.space, mode)
    if not rows:
        error = "받을 사람이 없어요. 동학년 명부에 반을 고른 선생님이 없어요."
        return render(request, "collect/list.html", _ctx(request, error), status=400)
    with transaction.atomic():
        c = Collection.objects.create(space=request.space, created_by=request.member, title=src.title,
                                      source=src.source, unit_head=src.unit_head, multi=src.multi,
                                      allow_outside=src.allow_outside, footer_on=src.footer_on)
        Field.objects.bulk_create([Field(collection=c, pos=f.pos, name=f.name, kind=f.kind, options=f.options)
                                   for f in Field.objects.in_space(request.space).filter(collection=src)])
        Row.objects.bulk_create([Row(collection=c, pos=i, unit=r["unit"], who=r["who"], member=r["member"])
                                 for i, r in enumerate(rows)])
    r = redirect(f"/s/{request.space.token}/c/{c.key}/")
    r.status_code = 303  # 새로 고침해도 다시 만들지 않게
    return r
