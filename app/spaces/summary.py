"""L1 학교 요약 한 장(prototype summaryCard, 26번 4장 교감용 요약). 합계 숫자만, 전체 회원이 같은 값을 본다.

- 제출 완료율: 합본과 같은 판정(submit._units). 그룹 칸은 한 칸, 여러 줄 양식은 받는 곳마다 한 칸.
- 공지 확인율: 공지마다 현황과 같은 값(seen.statuses)의 평균. 대상이 0명인 공지는 평균에서 뺀다.
  전체 회원이 보는 숫자라, 대상 합계가 ACK_MIN_TARGETS 미만이면 숨긴다(합계로 개인을 짐작할 수 있다). 쓴 사람·관리자 현황 화면과 무관한 요약만의 규칙.
- 평균 제출 시간은 넣지 않는다. Cell.at은 마지막으로 고친 시각이라 처음 낸 때를 알 수 없다.
"""
from datetime import date, timedelta

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from collect.lists import _past
from collect.models import Collection, Field
from collect.submit import _matrix, _units, unit_rows
from notices.models import Notice
from notices.seen import statuses

from .gates import no_org, school_required
from .models import Member

ACK_MIN_TARGETS = 10  # 리드 결정: 이번 학기 확인 필수 공지의 대상 합계가 이보다 적으면 '—'


def semester(d):
    """그 날짜가 든 학기의 (첫날, 다음 학기 첫날). 3~8월·9~2월."""
    if 3 <= d.month <= 8:
        return date(d.year, 3, 1), date(d.year, 9, 1)
    year = d.year if d.month >= 9 else d.year - 1
    return date(year, 9, 1), date(year + 1, 3, 1)


def period_text(d):
    first, end = semester(d)
    last = end - timedelta(days=1)
    return f"{first.year}년 {first.month}월 ~ {last.year}년 {last.month}월"


def _pct(a, b):
    return round(a / b * 100) if b else 0


def figures(space):
    """이번 학기 합계. 쿼리 수는 건수와 무관하다."""
    now = timezone.now()
    first, end = semester(timezone.localdate(now))
    span = {"created_at__date__gte": first, "created_at__date__lt": end}

    cs = list(Collection.objects.in_space(space).filter(**span)
              .annotate(inputs=Count("fields", filter=~Q(fields__kind=Field.Kind.CALC))))
    rows = unit_rows(cs)
    n_done = n_rows = n_open = 0
    for c in cs:
        units = _units(c, rows[c.pk], need=c.inputs if _matrix(c) else 1)
        done = sum(u["done"] for u in units)
        n_done += done
        n_rows += len(units)
        n_open += done < len(units) and not _past(c, now)

    must = list(Notice.objects.in_space(space).filter(must=True, **span))
    counted = [s for s in statuses(space, must).values() if s["total"]]
    rates = [s["seen"] / s["total"] for s in counted]
    ack_hidden = bool(counted) and sum(s["total"] for s in counted) < ACK_MIN_TARGETS

    people = Member.objects.in_space(space).aggregate(n=Count("pk"), groups=Count("group", distinct=True, filter=~Q(group="")))
    return {"n_collections": len(cs), "n_open": n_open, "n_done": n_done, "n_rows": n_rows, "done_pct": _pct(n_done, n_rows),
            "n_must": len(rates), "ack_hidden": ack_hidden,
            "ack_pct": None if ack_hidden else round(sum(rates) / len(rates) * 100) if rates else 0,
            "n_members": people["n"], "n_groups": people["groups"]}


@never_cache
@require_GET
@school_required
@no_org
def sheet(request):
    """한 장 인쇄(A4 세로). 학교 이름·기간·결재란과 숫자 카드, 안내 문구."""
    return render(request, "spaces/summary.html", {"space": request.space, "sum": figures(request.space),
                                                   "period": period_text(timezone.localdate())})
