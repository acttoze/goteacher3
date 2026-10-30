"""구글 로그인(OAuth2 인가 코드 흐름, 표준 라이브러리만) + 기기 토큰 발급·해제."""
import json
import logging
import secrets
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .models import DEVICE_AGE, DEVICE_COOKIE, DeviceToken, hash_token

log = logging.getLogger(__name__)

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def _redirect_uri():
    return settings.SITE_URL + "/auth/google/callback"


def _http_json(req):
    with urlopen(req, timeout=10) as r:
        return json.load(r)


def _safe_next(url):
    return url if url and url_has_allowed_host_and_scheme(url, allowed_hosts=set()) else "/"


def _fail(request, status, message):
    return render(request, "accounts/home.html", {"error": message}, status=status)


@require_GET
def home(request):
    return render(request, "accounts/home.html", {"next": _safe_next(request.GET.get("next"))})


@require_GET
def google_login(request):
    if not settings.GOOGLE_CLIENT_ID:
        return _fail(request, 503, "구글 로그인이 아직 설정되지 않았어요.")
    state = secrets.token_urlsafe(24)
    request.session["google_state"] = state
    request.session["google_next"] = _safe_next(request.GET.get("next"))
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",  # 학교 PC는 여러 계정이 섞여 있다
    }
    return redirect(f"{GOOGLE_AUTH}?{urlencode(params)}")


@require_GET
def google_callback(request):
    expected = request.session.pop("google_state", None)
    next_url = request.session.pop("google_next", "/")
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not (expected and state and code and secrets.compare_digest(expected.encode(), state.encode())):
        return _fail(request, 400, "로그인을 확인하지 못했어요. 처음부터 다시 해 주세요.")

    try:
        token = _http_json(Request(GOOGLE_TOKEN, method="POST", data=urlencode({
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        }).encode()))
        info = _http_json(Request(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {token['access_token']}"}))
        sub, email, name = str(info["sub"]), info.get("email", ""), info.get("name", "")
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("구글 로그인 실패: %s", type(e).__name__)  # 코드·토큰 값은 남기지 않는다
        return _fail(request, 502, "구글과 연결하지 못했어요. 잠시 뒤 다시 해 주세요.")

    user, created = get_user_model().objects.get_or_create(username=f"google:{sub}")
    user.email, user.first_name = email[:254], name[:150]
    if created:
        user.set_unusable_password()
    user.save()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    response = redirect(next_url)
    response.set_cookie(
        DEVICE_COOKIE,
        DeviceToken.issue(user),
        max_age=int(DEVICE_AGE.total_seconds()),
        httponly=True,
        secure=settings.SITE_URL.startswith("https://"),
        samesite="Lax",
    )
    return response


@require_POST
def logout_view(request):
    raw = request.COOKIES.get(DEVICE_COOKIE)
    if raw:
        DeviceToken.objects.filter(token_hash=hash_token(raw)).delete()
    logout(request)
    response = redirect("/")
    response.delete_cookie(DEVICE_COOKIE)
    return response
