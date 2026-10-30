from django.contrib.auth import login

from .models import DEVICE_COOKIE, DeviceToken


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
