"""구글 로그인(OAuth2 인가 코드 흐름, 표준 라이브러리만) + 기기 토큰 발급·해제."""
import base64
import hashlib
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

from spaces.models import Member, absorb_guest

from .models import DEVICE_AGE, DEVICE_COOKIE, DeviceToken, hash_token, is_guest

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
    verifier = secrets.token_urlsafe(48)  # PKCE: 원문은 세션에만, 구글에는 S256 해시만
    request.session["google_verifier"] = verifier
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "prompt": "select_account",  # 학교 PC는 여러 계정이 섞여 있다
    }
    return redirect(f"{GOOGLE_AUTH}?{urlencode(params)}")


@require_GET
def google_callback(request):
    expected = request.session.pop("google_state", None)
    next_url = request.session.pop("google_next", "/")
    verifier = request.session.pop("google_verifier", None)
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not (expected and verifier and state and code and secrets.compare_digest(expected.encode(), state.encode())):
        return _fail(request, 400, "로그인을 확인하지 못했어요. 처음부터 다시 해 주세요.")

    try:
        token = _http_json(Request(GOOGLE_TOKEN, method="POST", data=urlencode({
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }).encode()))
        info = _http_json(Request(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {token['access_token']}"}))
        sub, email, name = str(info["sub"]), info.get("email", ""), info.get("name", "")
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("구글 로그인 실패: %s", type(e).__name__)  # 코드·토큰 값은 남기지 않는다
        return _fail(request, 502, "구글과 연결하지 못했어요. 잠시 뒤 다시 해 주세요.")

    User = get_user_model()
    user = User.objects.filter(username=f"google:{sub}").first()
    guest = request.user if is_guest(request.user) else None
    if guest and user is None:  # 이름만 들어온 사람이 구글을 처음 잇는다: 같은 사용자 그대로
        user, guest = guest, None
        user.username = f"google:{sub}"
        # 손님일 때 받은 다른 PC의 기기 토큰·세션은 모두 끊는다(같은 반·이름으로 들어온 남이 구글 계정을 얻지 않게).
        # 이 PC 토큰은 아래 set_device_cookie가 새로 준다. 비밀번호 해시를 바꾸면 다른 세션의 인증 해시가 맞지 않게 된다.
        DeviceToken.objects.filter(user=user).delete()
        user.set_unusable_password()
    elif guest:  # 이미 있는 구글 계정에 합친다. R1: 교사 한 명은 동학년 하나
        if not Member.objects.filter(user=user).exclude(space__members__user=guest).exists():
            absorb_guest(guest, user)
        else:
            return _fail(request, 409, "이 구글 계정은 이미 다른 동학년에 들어가 있어요. 한 선생님은 동학년 하나만 쓸 수 있어요.")
    if user is None:
        user = User(username=f"google:{sub}")
        user.set_unusable_password()
    if info.get("email_verified") is not True:
        email = ""  # 확인 안 된 이메일은 저장하지 않는다(신원 확인은 sub로만)
    user.email, user.first_name = email[:254], name[:150]
    user.save()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return set_device_cookie(request, redirect(next_url), user)


def set_device_cookie(request, response, user):
    """이 PC를 user로 1년 기억한다. 같은 PC의 예전 토큰은 지우고 새로 발급(쌓이지 않게)."""
    old = request.COOKIES.get(DEVICE_COOKIE)
    if old:
        DeviceToken.objects.filter(token_hash=hash_token(old)).delete()
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
