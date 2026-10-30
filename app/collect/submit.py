"""C5 받는 사람 제출 — 링크 하나(/c/<link_token>) → 줄 고르기 → 내기·고치기. 동작은 prototype/recv.html, 모습은 design/submit.html.

- 인증 없음. 대신 내기 허용(누구나 어느 줄이든 고른다). 내는 사람(by)·시각(at)은 Cell에 남는다.
- "이 PC가 기억하는 이름"은 세션(서버, HttpOnly 쿠키)에 둔다. 리드 결정(기기 토큰은 쿠키만, localStorage 안 씀)과 같은 방식.
  공간 회원으로 로그인해 있으면 그 회원 이름을 쓴다.
- 받는 사람은 다른 줄의 값을 보지 못한다(냈는지만). 이 세션이 낸 줄만 고칠 때 값을 미리 채운다.
- Cell은 낼 때만 만든다. 자동 계산(calc) 칸은 입력이 아니어서 저장하지 않는다(합본이 계산).
- 제출 값은 로그·오류 보고에 남기지 않는다(sensitive_post_parameters, 예외 문구에 값 없음).
"""
import re
from datetime import date
from functools import wraps

from django.db import transaction
from django.db.models import Count, Max
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from spaces.models import Member
from spaces.views import PEOPLE_COLORS, _clean_name

from .models import Cell, Collection, Field, Row, get_by_link_or_404
from .paste import clean_text
from .presets import PRESETS
from .recipients import OutsideClosed, add_outside_row, pick_row

SESSION_BY = "gt_by"  # 이 브라우저에서 마지막으로 쓴 내는 사람 이름
SESSION_SENT = "gt_sent"  # {줄 키: 이 브라우저가 낸 뒤의 마지막 Cell.at}. 같을 때만 고칠 값을 미리 채움
SENT_MAX = 200
LINES_MAX = 50  # multi: 한 번에 내는 줄 상한
VALUE_MAX = 1000
NA = "해당 없음"  # 연수 이수(prototype matrixForm) 체크
ASK = {Field.Kind.NUMBER: "숫자로 써 주세요. 없으면 0이에요.", Field.Kind.SELECT: "골라 주세요.", Field.Kind.DATE: "골라 주세요."}
UNIT_RE = re.compile(r"^(.*?)\s*\(([^()]{1,6})\)$")  # '참가(명)' → 참가 / 명


def link_view(view):
    """링크 토큰으로 모으기를 찾는다. 없는 링크·다른 모으기 줄이면 design의 '링크를 찾을 수 없어요'(404)."""

    @wraps(view)
    def inner(request, link_token, **kw):
        try:
            return view(request, get_by_link_or_404(link_token), **kw)
        except Http404:
            return render(request, "collect/recv.html", {"view": "notfound"}, status=404)

    return inner


def save_cell(row, field, value, by):
    """줄×칸 값 하나를 넣거나 바꾼다. 칸과 줄이 같은 모으기여야 한다(DB는 막지 않는다)."""
    if row.collection_id != field.collection_id:
        raise ValueError("칸과 줄의 모으기가 다릅니다.")
    Cell.objects.update_or_create(row=row, field=field, defaults={"value": value, "by": by})


def _clean(field, raw):
    """(저장할 값, 오류 문구). 값은 오류 문구에 넣지 않는다."""
    v = clean_text(raw or "")[:VALUE_MAX]
    if not v:
        return "", None
    if field.kind == Field.Kind.NUMBER:
        n = v.replace(",", "").replace(" ", "")
        return (n, None) if re.fullmatch(r"\d+(\.\d+)?", n) else ("", "숫자로 써 주세요. 없으면 0이에요.")
    if field.kind == Field.Kind.SELECT:
        return (v, None) if v in field.options else ("", "보기에서 골라 주세요.")
    if field.kind == Field.Kind.DATE:
        try:
            return date.fromisoformat(v).isoformat(), None
        except ValueError:
            return "", "날짜를 골라 주세요."
    if field.kind == Field.Kind.URL:
        return (v, None) if re.match(r"https?://\S+$", v) else ("", "http:// 나 https:// 로 시작하는 주소를 붙여넣어 주세요.")
    return v, None


def _matrix(c):
    """연수 이수 양식(prototype c.kind === 'matrix'): 다 못 채워도 저장, 칸마다 '해당 없음', 다 채워야 제출함."""
    return c.source == Collection.Source.PRESET and c.unit_head == PRESETS["training"]["unit_head"]


def _required(c, f):
    """prototype 규칙: 모든 칸 필수, 이름이 '비고'인 칸만 예외. 여러 줄은 구입 링크(url)만 선택. 연수 이수는 모두 선택."""
    if _matrix(c) or f.name.strip() == "비고":
        return False
    return not (c.multi and f.kind == Field.Kind.URL)


def _need(c):
    """제출함으로 셀 칸 수: 연수 이수는 입력 칸 전부, 나머지는 하나라도."""
    return c.fields.exclude(kind=Field.Kind.CALC).count() if _matrix(c) else 1


def _label(field):
    m = UNIT_RE.match(field.name)
    return (m.group(1), m.group(2)) if m and field.kind == Field.Kind.NUMBER else (field.name, "")


def _group(c, row):
    """multi면 같은 받는 곳(unit·who·member) 줄 묶음, 아니면 그 줄 하나. 첫 줄이 명부 줄."""
    if not c.multi:
        return [row]
    return list(c.rows.filter(unit=row.unit, who=row.who, member=row.member_id))


def _units(c):
    """고르기 목록: 받는 곳마다 하나(multi는 첫 줄). 냈는지·마지막 낸 시각만(값 없음)."""
    rows = c.rows.annotate(n=Count("cells"), last=Max("cells__at")).order_by("pos", "id")
    out, seen, need = [], {}, _need(c)
    for r in rows:
        k = (r.unit, r.who, r.member_id) if c.multi else r.pk
        if k in seen:
            u = seen[k]
            u["done"] = u["done"] or r.n >= need
            u["at"] = max(filter(None, [u["at"], r.last]), default=None)
            continue
        seen[k] = u = {"row": r, "done": r.n >= need, "at": r.last}
        out.append(u)
    for u in out:
        u["late"] = bool(c.due and u["at"] and u["at"] > c.due)
    return out


def _me(request, c):
    """(이 공간 회원, 기억하는 이름). 로그인한 회원이면 그 이름이 먼저."""
    member = None
    if request.user.is_authenticated:
        member = Member.objects.in_space(c.space).filter(user=request.user).first()
    return member, (member.name if member else request.session.get(SESSION_BY, ""))


def _mine(row, member, name):
    if member:
        return row.member_id == member.pk
    return bool(name) and row.who == name


def _head(c):
    units = _units(c)
    done = sum(u["done"] for u in units)
    return {"c": c, "units": units, "n_done": done, "n_total": len(units),
            "pct": round(done / len(units) * 100) if units else 0,
            "per": "반" if c.unit_head[:1] == ["학년반"] else "명", "owner": c.created_by,
            "school": c.space.school.name, "grade": c.space.grade}


def _colors(c):
    members = Member.objects.in_space(c.space).order_by("joined_at", "pk").values_list("pk", flat=True)
    return {pk: PEOPLE_COLORS[i % len(PEOPLE_COLORS)] for i, pk in enumerate(members)}


def _page(request, ctx, status=200):
    tpl = "collect/_recv_main.html" if request.headers.get("HX-Request") else "collect/recv.html"
    return render(request, tpl, ctx, status=status)


@require_http_methods(["GET"])
@link_view
def pick(request, c):
    ctx = _head(c)
    if c.closed_at:
        return _page(request, {**ctx, "view": "closed"})
    member, name = _me(request, c)
    colors = _colors(c)
    for u in ctx["units"]:
        u["mine"] = _mine(u["row"], member, name)
        u["color"], u["ink"] = colors.get(u["row"].member_id, ("var(--ink)", False))
    ctx.update(view="pick", outside_error=None)
    return _page(request, ctx)


@sensitive_post_parameters()
@require_POST
@link_view
def outside(request, c):
    """명부 밖 사람이 이름을 써서 줄을 만든다(C4 add_outside_row). 쓴 이름을 내는 사람으로 기억한다."""
    try:
        row = add_outside_row(c, request.POST.get("name", ""))
    except OutsideClosed:
        return _page(request, {**_head(c), "view": "closed" if c.closed_at else "pick",
                               "outside_error": "이 모으기는 명부 밖 이름을 더 받지 않아요."}, status=403)
    except ValueError:
        ctx = {**_head(c), "view": "pick", "outside_error": "이름을 써 주세요."}
        return _page(request, ctx, status=400)
    request.session[SESSION_BY] = row.who
    r = redirect(f"/c/{c.link_token}/u/{row.key}")
    r.status_code = 303  # 새로 고침해도 다시 만들지 않게
    return r


def _stamp(group):
    """줄 묶음의 마지막 낸 시각(문자열). 다른 사람이 덮어쓰면 바뀐다."""
    at = Cell.objects.filter(row__in=group).aggregate(m=Max("at"))["m"]
    return at.isoformat() if at else ""


def _sent_state(request, group):
    """(이 세션이 마지막으로 낸 그대로인지, 이 세션이 냈는데 다른 분이 고쳤는지)."""
    sent = request.session.get(SESSION_SENT)
    mine = sent.get(group[0].key) if isinstance(sent, dict) else None
    if not mine:
        return False, False
    same = mine == _stamp(group)
    return same, not same


def _line_values(group, inputs, sent):
    """고치기 화면에 채울 값. 이 세션이 낸 줄만(다른 사람 값은 보이지 않는다)."""
    if not sent:
        return [{"key": group[0].key, "vals": {}}]
    cells = Cell.objects.filter(row__in=group, field__in=inputs).values_list("row_id", "field_id", "value")
    by_row = {}
    for rid, fid, v in cells:
        by_row.setdefault(rid, {})[fid] = v
    lines = [{"key": r.key, "vals": by_row[r.pk]} for r in group if r.pk in by_row]
    return lines or [{"key": group[0].key, "vals": {}}]


def _form_ctx(request, c, row, group, inputs, lines, errors=None, by=None):
    member, name = _me(request, c)
    fields = list(c.fields.all())
    head_row = group[0]
    sent, changed = _sent_state(request, group)
    done = Cell.objects.filter(row__in=group).count() >= _need(c)
    last = Cell.objects.filter(row__in=group).aggregate(m=Max("at"))["m"]
    mine = _mine(head_row, member, name)
    cols = [{"f": f, "label": _label(f)[0], "unit": _label(f)[1], "calc": f.kind == Field.Kind.CALC} for f in fields]
    for ln in lines:
        ln["cells"] = [{"col": col, "v": ln["vals"].get(col["f"].pk, ""),
                        "err": (errors or {}).get((ln["key"], col["f"].pk))} for col in cols]
    blank = {"key": "", "cells": [{"col": col, "v": "", "err": None} for col in cols]}
    return {**_head(c), "view": "form", "row": head_row, "cols": cols, "lines": lines, "blank": blank,
            "n_main": sum(col["f"].kind != Field.Kind.URL for col in cols), "done": done,
            "last": last, "sent": sent, "changed": changed, "mine": mine, "by": name if by is None else by,
            "matrix": _matrix(c), "na": NA, "errors": errors or {}, "form_error": (errors or {}).get("form"), "n_inputs": len(inputs)}


@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
@link_view
def unit(request, c, key):
    row = pick_row(c, key)
    group = _group(c, row)
    inputs = [f for f in c.fields.all() if f.kind != Field.Kind.CALC]
    if c.closed_at:
        return _page(request, {**_head(c), "view": "closed"}, status=403 if request.method == "POST" else 200)
    if request.method == "GET":
        sent, _ = _sent_state(request, group)
        return _page(request, _form_ctx(request, c, row, group, inputs, _line_values(group, inputs, sent)))

    by = _clean_name(request.POST.get("by", ""))
    if c.multi:
        keys = request.POST.getlist("line")
        raw = {f.pk: request.POST.getlist(f"f{f.pos}") for f in inputs}
        group_keys = {r.key for r in group}
        bad_key = any(k and k not in group_keys for k in keys) or len(set(k for k in keys if k)) != len([k for k in keys if k])
        if len(keys) > LINES_MAX or bad_key or any(len(v) != len(keys) for v in raw.values()):
            return HttpResponseBadRequest("잘못된 요청입니다.")
        posted = [{"key": k, "vals": {f.pk: raw[f.pk][i] for f in inputs}} for i, k in enumerate(keys)]
        posted = [ln for ln in posted if any(clean_text(v) for v in ln["vals"].values())]
    else:
        posted = [{"key": row.key, "vals": {f.pk: NA if _matrix(c) and request.POST.get(f"na{f.pos}") == "on"
                                            else request.POST.get(f"f{f.pos}", "") for f in inputs}}]

    errors, clean_lines = {}, []
    for ln in posted:
        vals = {}
        for f in inputs:
            v, err = _clean(f, ln["vals"][f.pk])
            if not err and not v and _required(c, f):
                err = ASK.get(f.kind, "써 주세요.")
            if err:
                errors[(ln["key"], f.pk)] = err
            vals[f.pk] = v
        clean_lines.append({"key": ln["key"], "vals": vals})
    if not _matrix(c) and (not posted or not any(v for ln in clean_lines for v in ln["vals"].values())):
        errors["form"] = "한 칸 이상 채워 주세요."
    if errors:
        lines = posted or [{"key": group[0].key, "vals": {}}]
        return _page(request, _form_ctx(request, c, row, group, inputs, lines, errors, by), status=400)

    with transaction.atomic():
        locked = Collection.objects.select_for_update().get(pk=c.pk)  # 마감과 동시에 내는 경우
        if locked.closed_at:
            return _page(request, {**_head(c), "view": "closed"}, status=403)
        by_key = {r.key: r for r in group}
        kept = set()
        last_pos = c.rows.aggregate(m=Max("pos"))["m"] or 0
        for ln in clean_lines:
            r = by_key.get(ln["key"])
            if r is None:  # multi 새 줄: 같은 받는 곳으로 맨 끝에
                last_pos += 1
                r = Row.objects.create(collection=c, pos=last_pos, unit=row.unit, who=row.who, member=row.member)
            kept.add(r.pk)
            for f in inputs:
                if _matrix(c) and not ln["vals"][f.pk]:  # 연수 이수: 채운 칸만(지운 칸은 값도 지움)
                    r.cells.filter(field=f).delete()
                else:
                    save_cell(r, f, ln["vals"][f.pk], by)
        for r in group:
            if r.pk in kept:
                continue
            if r.pk == group[0].pk:
                r.cells.all().delete()  # 명부 줄은 남기고 값만
            else:
                r.delete()
        stamp = _stamp(group[:1] + [r for r in Row.objects.filter(pk__in=kept)])  # 잠금 안에서: 다른 제출과 섞이지 않게

    if by:
        request.session[SESSION_BY] = by
    sent = request.session.get(SESSION_SENT)
    sent = {k: v for k, v in (sent.items() if isinstance(sent, dict) else []) if k != group[0].key}
    sent[group[0].key] = stamp
    request.session[SESSION_SENT] = dict(list(sent.items())[-SENT_MAX:])
    names = {f.pk: f.name for f in inputs}
    summary = [[(names[fid], v) for fid, v in ln["vals"].items()] for ln in clean_lines]
    if c.multi:  # 한 줄에 채운 값만 이어 쓴다
        summary = [" · ".join(v for _, v in line if v) for line in summary]
    at = timezone.localtime()
    ctx = {**_head(c), "view": "done", "row": group[0], "summary": summary, "by": by, "at": at,
           "late": bool(c.due and at > c.due), "proxy": bool(by) and by != row.who,
           "partial": Cell.objects.filter(row=group[0]).count() < _need(c)}
    return _page(request, ctx)
