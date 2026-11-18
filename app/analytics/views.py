"""V10 운영자 고객 분석(/analytics). 운영자(is_staff)만, 그 외 404. 이름은 내지 않는다(학교·소속까지)."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from feedback.views import staff_only
from library.views import _frame

from .stats import build

PERIODS = [("1", "오늘", 1), ("7", "7일", 7), ("30", "30일", 30), ("all", "전체", None)]


@never_cache
@require_safe
@staff_only
def page(request):
    d = request.GET.get("d", "7")
    key, label, days = next((p for p in PERIODS if p[0] == d), PERIODS[1])
    return render(request, "analytics/page.html", {**_frame(request, page="analytics"), **build(days),
                                                   "periods": PERIODS, "d": key, "period_label": label})
