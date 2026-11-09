"""요청 본문 상한과 남은 본문 버리기. CSRF 미들웨어가 POST 본문을 먼저 풀기 때문에 맨 앞에서 선언 크기만 본다(익명 POST로 디스크·메모리를 채우지 못하게).
값이 없거나 숫자가 아니면 그대로 넘긴다(청크 전송 등은 Traefik·gunicorn 몫). 맨 앞이라 공통 CSP를 직접 붙인다.
파일을 받는 주소만 높게 둔다. 주소와 값은 앞단 Traefik 라우터(ops/compose.yml)와 같다.

뷰가 본문을 읽기 전에 응답하면(로그아웃 리다이렉트·없는 공간 404·413) gunicorn이 읽지 않은 본문이 남은 채 연결을 닫아 Traefik이 502로 본다.
그래서 응답을 돌려주기 전에 남은 본문을 조각으로 읽어 버린다. Traefik이 본문을 다 받은 뒤 넘기므로(버퍼링) 기다림은 짧고 메모리에 쌓지 않는다.
"""
import re

from django.conf import settings
from django.http import HttpResponse

from .csp import CSP

PAGE = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>올린 내용이 너무 커요 · 고티처</title>'
        "<p>올린 내용이 너무 커서 받지 못했어요. 아무것도 저장되지 않았어요. 뒤로 가서 파일이나 붙여넣은 표를 줄여 다시 올려 주세요.</p></html>")
_UPLOADS = (
    (re.compile(r"/s/[^/]+/board/(?:new|[^/]+/edit)"), "MAX_NOTICE_REQUEST_BYTES"),
    (re.compile(r"/s/[^/]+/today/e/[^/]+/files"), "MAX_NOTICE_REQUEST_BYTES"),  # P2 일정 첨부(공지 첨부와 같은 한도)
    (re.compile(r"/s/[^/]+/hwp/upload"), "MAX_HWP_REQUEST_BYTES"),
    (re.compile(r"/s/[^/]+/(?:repairs/new|repairs/[^/]+/edit|meals/[^/]+/upload)|/m/[^/]+"), "MAX_PHOTO_REQUEST_BYTES"),
)
CHUNK = 64 * 1024
DRAIN_MAX = 101 * 1024 * 1024  # 이보다 크다고 선언한 본문은 앞단을 지나올 수 없다. 버리지 않고 끊는다


def _limit(path):
    for pattern, name in _UPLOADS:
        if pattern.fullmatch(path):
            return getattr(settings, name)
    return settings.MAX_REQUEST_BYTES


def _drain(request, declared):
    left = declared
    if not 0 < left <= DRAIN_MAX:
        return
    try:
        while left > 0:
            piece = request.read(min(CHUNK, left))
            if not piece:
                return
            left -= len(piece)
    except Exception:  # 끊긴 연결 등. 응답은 이미 정해졌고 버리기 실패는 그 응답과 상관없다
        return


class BodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > _limit(request.path):
            response = HttpResponse(PAGE, status=413, content_type="text/html; charset=utf-8")
            response["Content-Security-Policy"] = CSP
        else:
            response = self.get_response(request)
        _drain(request, declared)
        return response
