"""V10 화면 연 횟수: 계정 회원이 페이지(GET·200·HTML, htmx 조각 아님)를 열면 그날·그 화면 모양에 +1.

경로는 주소 규칙 모양만(토큰·키·쿼리 없음). 운영·정적·업체·TV·폰 화면은 세지 않는다.
기록이 실패해도 응답은 그대로 나간다. 테스트(TestCase)에서는 captureOnCommitCallbacks(execute=True) 안에서만 쌓인다.
"""
import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import is_account_member
from feedback.views import route_shape

from .models import DailyUse

log = logging.getLogger(__name__)
SKIP = ("/analytics", "/ops/", "/static/", "/healthz", "/v/", "/tv/", "/m/")


def _counts(request, response):
    return (request.method == "GET" and response.status_code == 200
            and response.get("Content-Type", "").startswith("text/html")
            and not request.headers.get("HX-Request")
            and not request.path.startswith(SKIP)
            and getattr(request, "resolver_match", None) is not None
            and is_account_member(request.user))


def _add(key):
    try:
        with transaction.atomic():
            if DailyUse.objects.filter(**key).update(n=F("n") + 1):
                return
            try:
                with transaction.atomic():
                    DailyUse.objects.create(**key, n=1)
            except IntegrityError:  # 같은 사람이 같은 순간 두 번: 먼저 만든 줄에 더한다
                DailyUse.objects.filter(**key).update(n=F("n") + 1)
    except Exception:
        log.warning("analytics: use not recorded")


class UsageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if _counts(request, response):
                route = request.resolver_match.route
                space = getattr(request, "space", None) if "<str:space_token>" in route else None
                key = {"day": timezone.localdate(), "user": request.user, "space": space, "route": route_shape(route)}
                # 요청이 커밋된 뒤에 센다(autocommit인 운영에서는 바로). 화면의 쿼리·트랜잭션과 섞이지 않는다
                transaction.on_commit(lambda: _add(key))
        except Exception:
            log.warning("analytics: use not recorded")
        return response
