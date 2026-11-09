from urllib.parse import quote

from django.contrib.auth import login
from django.http import HttpResponse
from django.shortcuts import redirect

from .models import DEVICE_COOKIE, MUST_CHANGE, DeviceToken


class DeviceTokenMiddleware:
    """세션이 없고 기기 쿠키가 있으면 그 사용자로 다시 로그인한다. 맞지 않는 쿠키는 지운다."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.COOKIES.get(DEVICE_COOKIE)
        bad = False
        if raw and not request.user.is_authenticated:
            user = DeviceToken.find_user(raw)
            if user:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            else:
                bad = True
        response = self.get_response(request)
        if bad and DEVICE_COOKIE not in response.cookies:
            response.delete_cookie(DEVICE_COOKIE)
        return response


class PasswordChangeMiddleware:
    """임시 비밀번호로 들어온 세션(P1 S6): 비밀번호를 바꾸기 전에는 바꾸기 화면·로그아웃·정적 파일만."""

    OPEN = ("/account/password", "/auth/logout", "/static/", "/privacy", "/terms")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get(MUST_CHANGE) and not request.path.startswith(self.OPEN):
            to = "/account/password?next=" + quote(request.get_full_path() if request.method == "GET" else "/")
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = to
                return response
            return redirect(to)
        return self.get_response(request)
