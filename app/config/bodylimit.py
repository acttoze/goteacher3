"""요청 본문 전역 상한. CSRF 미들웨어가 POST 본문을 먼저 풀기 때문에 맨 앞에서 선언 크기만 본다(익명 POST로 디스크·메모리를 채우지 못하게).
값이 없거나 숫자가 아니면 그대로 넘긴다(청크 전송 등은 Traefik·gunicorn 몫). 맨 앞이라 공통 CSP를 직접 붙인다."""
from django.conf import settings
from django.http import HttpResponse

from .csp import CSP

PAGE = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>올린 내용이 너무 커요 · 고티처</title>'
        "<p>올린 내용이 너무 커서 받지 못했어요. 아무것도 저장되지 않았어요. 뒤로 가서 파일을 줄여 다시 올려 주세요.</p></html>")


class BodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > settings.MAX_REQUEST_BYTES:
            r = HttpResponse(PAGE, status=413, content_type="text/html; charset=utf-8")
            r["Content-Security-Policy"] = CSP
            return r
        return self.get_response(request)
