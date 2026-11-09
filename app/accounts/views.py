"""구글 로그인(OAuth2 인가 코드 흐름, 표준 라이브러리만) + 아이디 로그인·가입(P1) + 기기 토큰 발급·해제."""
import base64
import hashlib
import json
import logging
import secrets
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe

from config.ratelimit import allow, client_ip
from spaces.models import INVITE, Member, absorb_guest

from .models import (
    DEVICE_AGE,
    DEVICE_COOKIE,
    GUEST_ORIGIN,
    ID_RE,
    MUST_CHANGE,
    DeviceToken,
    PasswordChangeRequired,
    hash_token,
    is_guest,
)

log = logging.getLogger(__name__)

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

# 시도 제한(P1): 메일 인증이 없어 비밀번호 대입 방어가 유일한 장치. 로그인은 실패만 센다. 학교는 IP 하나(NAT)를 같이 써서 IP 쪽은 넉넉히.
# 잠금은 (아이디+IP) 묶음: 구 아이디 목록으로 남의 계정을 잠그는 공격이 본인의 다른 PC까지 막지 않게. 아이디 전체 한도는 따로 훨씬 높게.
# 캐시가 죽어도 프로세스 메모리로 센다(fallback).
LOGIN_WINDOW = 15 * 60
LOGIN_FAILS_ID = 10  # 아이디+IP
LOGIN_FAILS_ID_ALL = 100  # 아이디(모든 IP)
LOGIN_FAILS_IP = 50
SIGNUPS_PER_HOUR_IP = 30  # 가입 POST(결과와 상관없이). 연수 때 한 학교가 함께 가입해도 걸리지 않게
LOGIN_WRONG = "아이디나 비밀번호가 맞지 않아요."
LOGIN_LOCKED = "로그인을 너무 많이 틀렸어요. 15분 뒤 다시 해 주세요."
PASSWORD_RULE = "비밀번호는 8자 이상, 흔하지 않은 것으로 써 주세요."
ID_TAKEN = "이미 쓰는 아이디예요. 다른 아이디를 써 주세요."
GUEST_LINK = "이 PC에서는 이을 수 없어요. 처음 들어온 PC에서 이어 주세요. 다른 계정으로 들어오려면 먼저 이 PC에서 로그아웃해 주세요."


def _redirect_uri():
    return settings.SITE_URL + "/auth/google/callback"


def _http_json(req):
    with urlopen(req, timeout=10) as r:
        return json.load(r)


def _safe_next(url):
    return url if url and url_has_allowed_host_and_scheme(url, allowed_hosts=set()) else "/"


def _fail(request, status, message, **ctx):
    return render(request, "accounts/home.html", {"error": message, **ctx}, status=status)


@never_cache
@require_safe
def home(request):
    next_url = _safe_next(request.GET.get("next"))
    if request.user.is_authenticated and not is_guest(request.user):  # 이미 계정 회원: 시작 화면 대신 가려던 곳으로
        return redirect(next_url)
    # 손님이 계정을 이으러 왔으면 '이 PC 기억' 칸 없이 입장 때 고른 대로(구글 잇기와 같음), 머리말도 잇기 안내로
    return render(request, "accounts/home.html", {"next": next_url, "guest": is_guest(request.user)})


def _guest_conflict(guest, user):
    """손님 자리를 이미 있는 계정에 합친다. R1: 교사 한 명은 공간 하나라 합칠 수 없으면 오류 문구."""
    if not Member.objects.filter(user=user).exclude(space__members__user=guest).exists():
        absorb_guest(guest, user)
        return ""
    word = "공간" if Member.objects.filter(user=guest, space__kind="school").exists() else "동학년"
    return f"이 계정은 이미 다른 {word}에 들어가 있어요. 한 선생님은 {word} 하나만 쓸 수 있어요."


def _guest_owner(request, guest):
    """손님을 계정으로 이어도 되는 요청인가(S5): 손님을 처음 만든 세션이거나, 그 세션에서 받은 기기 토큰(origin)을 가진 PC.
    같은 반·이름으로 다른 PC에서 들어온 사람이 자리를 계정으로 가져가지 못하게(발급 순서는 보지 않는다)."""
    if request.session.get(GUEST_ORIGIN) == guest.pk:
        return True
    raw = request.COOKIES.get(DEVICE_COOKIE)
    return bool(raw and len(raw) <= 128
                and DeviceToken.objects.filter(user=guest, origin=True, token_hash=hash_token(raw)).exists())


def _guest_becomes(user, username):
    """이름만 들어온 손님이 처음 계정을 만든다: 같은 사용자 그대로 username만 바꾼다.
    손님일 때 받은 다른 PC의 기기 토큰은 모두 끊는다(같은 반·이름으로 들어온 남이 계정을 얻지 않게).
    이 PC 토큰은 remember_pc가 새로 준다. 부른 쪽이 비밀번호 해시를 바꿔 다른 세션도 끊긴다."""
    user.username = username
    DeviceToken.objects.filter(user=user).delete()


def _enter(request, user, remember, next_url):
    """로그인하고 가려던 곳으로. 손님이 계정을 이으면 login이 세션을 비우므로 열어 둔 초대(학교 공간 입장)는 이어 간다.
    임시 비밀번호를 받은 회원이면 이 세션은 바꾸기 화면만 쓰고, 기기 토큰은 주지 않는다(바꾸기 전 세션만 표시를 들고 있게)."""
    invite = request.session.get(INVITE)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    if invite:
        request.session[INVITE] = invite
    if PasswordChangeRequired.objects.filter(user=user).exists():
        request.session[MUST_CHANGE] = True
        remember = False
    return remember_pc(request, redirect(next_url), user, remember)


@require_GET
def google_login(request):
    if not settings.GOOGLE_CLIENT_ID:
        return _fail(request, 503, "구글 로그인이 아직 설정되지 않았어요.")
    state = secrets.token_urlsafe(24)
    request.session["google_state"] = state
    request.session["google_next"] = _safe_next(request.GET.get("next"))
    # 이 PC 기억(G3 S2): 시작 화면 체크(숨은 0 + 체크 1, 기본 끔 — 사용자 결정 11/3).
    # 값이 없으면 끔. 손님의 '구글로 이어 두기'는 입장 때 고른 대로(이 PC 기기 쿠키가 있나).
    request.session["google_remember"] = wants_remember(
        request.GET, bool(request.COOKIES.get(DEVICE_COOKIE)) if is_guest(request.user) else False)
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
    remember = request.session.pop("google_remember", False)
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
    if guest and not _guest_owner(request, guest):
        return _fail(request, 403, GUEST_LINK)
    if guest and user is None:  # 이름만 들어온 사람이 구글을 처음 잇는다
        user = guest
        _guest_becomes(user, f"google:{sub}")
        user.set_unusable_password()
    elif guest:  # 이미 있는 구글 계정에 합친다
        error = _guest_conflict(guest, user)
        if error:
            return _fail(request, 409, error)
    if user is None:
        user = User(username=f"google:{sub}")
        user.set_unusable_password()
    if info.get("email_verified") is not True:
        email = ""  # 확인 안 된 이메일은 저장하지 않는다(신원 확인은 sub로만)
    user.email, user.first_name = email[:254], name[:150]
    user.save()
    return _enter(request, user, remember, next_url)


def _find_id_member(username):
    """아이디(대소문자 무시)로 회원 찾기. 규칙에 안 맞으면 찾지 않는다(구글·손님 username은 ':'가 있어 걸리지 않는다)."""
    if not ID_RE.fullmatch(username):
        return None
    return get_user_model().objects.filter(username__iexact=username).first()


def _login_limits(request, username):
    u, ip = username.lower(), client_ip(request)
    return (("login-idip", f"{u}:{ip}", LOGIN_FAILS_ID), ("login-id", u, LOGIN_FAILS_ID_ALL), ("login-ip", ip, LOGIN_FAILS_IP))


def _count_fail(request, username):
    for scope, ident, limit in _login_limits(request, username):
        allow(scope, ident, limit, LOGIN_WINDOW, fallback=True)


def _locked(request, username):
    return not all(allow(scope, ident, limit, LOGIN_WINDOW, count=False, fallback=True)
                   for scope, ident, limit in _login_limits(request, username))


def _check_password(user, password):
    """S4: 계정 종류(구 해시·비활성)가 시간 차로 드러나지 않게, 틀릴 때는 기본 해셔 한 번만큼 비용을 치른다.
    (Django는 기본 해셔가 아닌 해시에 harden_runtime을 부르지 않는다.)"""
    if not user.is_active:
        make_password(password)
        return False
    default = user.password.startswith("pbkdf2_sha256$")
    ok = user.check_password(password)
    if not ok and not default:
        make_password(password)
    return ok


@never_cache
@require_POST
def id_login(request):
    """아이디·비밀번호 로그인(P1). 실패 문구는 아이디가 있는지 드러내지 않는다. 구버전 해시는 맞으면 새 해시로 바뀐다.
    같은 폼의 구글 단추(via=google)는 기억하기·돌아올 곳을 그대로 들고 구글 로그인으로 넘긴다."""
    next_url = _safe_next(request.POST.get("next"))
    if request.POST.get("via") == "google":
        q = {"next": next_url}
        if "remember" in request.POST:  # 손님이 잇는 중이면 칸이 없다: google_login이 입장 때 고른 대로
            q["remember"] = "1" if wants_remember(request.POST, False) else "0"
        return redirect("/auth/google/login?" + urlencode(q))
    username = request.POST.get("username", "").strip()[:40]
    password = request.POST.get("password", "")
    remember = wants_remember(request.POST, is_guest(request.user) and bool(request.COOKIES.get(DEVICE_COOKIE)))
    ctx = {"next": next_url, "username": username, "remember": remember}
    if _locked(request, username):
        return _fail(request, 429, LOGIN_LOCKED, **ctx)
    user = _find_id_member(username)
    if user is None:
        make_password(password)  # 없는 아이디도 같은 시간이 걸리게
    if user is None or not _check_password(user, password):
        _count_fail(request, username)
        return _fail(request, 400, LOGIN_WRONG, **ctx)
    if is_guest(request.user):
        if not _guest_owner(request, request.user):
            return _fail(request, 403, GUEST_LINK, **ctx)
        error = _guest_conflict(request.user, user)
        if error:
            return _fail(request, 409, error, **ctx)
    return _enter(request, user, remember, next_url)


@never_cache
@require_http_methods(["GET", "POST"])
def signup(request):
    """아이디 가입(P1): 아이디·비밀번호·비밀번호 확인·이름. 메일 인증 없음. 가입하면 바로 로그인하고 구글과 같은 흐름으로."""
    src = request.POST if request.method == "POST" else request.GET
    next_url = _safe_next(src.get("next"))
    if request.user.is_authenticated and not is_guest(request.user):
        return redirect(next_url)
    username = src.get("username", "").strip()[:40]
    name = " ".join(src.get("name", request.user.first_name if request.user.is_authenticated else "").split())[:40]
    remember = wants_remember(src, is_guest(request.user) and bool(request.COOKIES.get(DEVICE_COOKIE)))
    ctx = {"next": next_url, "username": username, "name": name, "remember": remember, "errors": {}}
    if request.method == "GET":
        return render(request, "accounts/signup.html", ctx)

    password, password2 = request.POST.get("password", ""), request.POST.get("password2", "")
    errors = ctx["errors"]
    if not allow("signup-ip", client_ip(request), SIGNUPS_PER_HOUR_IP, 3600, fallback=True):  # 결과와 상관없이 센다(S1)
        errors["username"] = "이곳에서 가입이 너무 많았어요. 1시간 뒤 다시 해 주세요."
        return render(request, "accounts/signup.html", ctx, status=429)
    if is_guest(request.user) and not _guest_owner(request, request.user):
        return render(request, "accounts/signup.html", {**ctx, "error": GUEST_LINK}, status=403)
    if not ID_RE.fullmatch(username):
        errors["username"] = "아이디는 영문·숫자·_ 3~20자로 써 주세요."
    elif _id_taken(username):
        errors["username"] = ID_TAKEN
    try:
        validate_password(password)
    except ValidationError:
        errors["password"] = PASSWORD_RULE
    if not errors.get("password") and password != password2:
        errors["password2"] = "비밀번호가 서로 달라요. 다시 써 주세요."
    if not name:
        errors["name"] = "선생님들에게 보일 이름을 써 주세요."
    if errors:
        return render(request, "accounts/signup.html", ctx, status=400)

    try:
        with transaction.atomic():
            if is_guest(request.user):
                user = request.user
                _guest_becomes(user, username)
            else:
                user = get_user_model()(username=username)
            user.first_name = name
            user.set_password(password)
            user.save()
    except IntegrityError:  # 동시에 같은 아이디(대소문자 무시 유일 인덱스, S2)
        errors["username"] = ID_TAKEN
        return render(request, "accounts/signup.html", ctx, status=400)
    return _enter(request, user, remember, next_url)


def _id_taken(username):
    """이미 있는 아이디(대소문자 무시)이거나, 이관 전 구 아이디 예약 목록(RESERVED_IDS_FILE)에 있으면 True."""
    if get_user_model().objects.filter(username__iexact=username).exists():
        return True
    path = settings.RESERVED_IDS_FILE
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return username.lower() in {line.strip().lower() for line in f}
    except OSError:  # 파일이 없으면 예약 끔
        return False


@never_cache
@require_http_methods(["GET", "POST"])
def force_password(request):
    """임시 비밀번호로 들어온 세션의 비밀번호 바꾸기(S6). 새 비밀번호만 받는다(방금 임시 비밀번호로 들어왔다).
    바꾸면 표시를 지우고 가려던 곳으로. 표시 없는 세션은 그냥 보낸다."""
    src = request.POST if request.method == "POST" else request.GET
    next_url = _safe_next(src.get("next"))
    if not (request.user.is_authenticated and request.session.get(MUST_CHANGE)):
        return redirect(next_url)
    errors = {}
    if request.method == "POST":
        new, new2 = request.POST.get("password", ""), request.POST.get("password2", "")
        try:
            validate_password(new, request.user)
        except ValidationError:
            errors["password"] = PASSWORD_RULE
        if not errors and new != new2:
            errors["password2"] = "새 비밀번호가 서로 달라요. 다시 써 주세요."
        if not errors:
            user = request.user
            user.set_password(new)
            user.save(update_fields=["password"])
            PasswordChangeRequired.objects.filter(user=user).delete()
            update_session_auth_hash(request, user)
            request.session.pop(MUST_CHANGE, None)
            return redirect(next_url)
    return render(request, "accounts/force_password.html", {"next": next_url, "errors": errors}, status=400 if errors else 200)


def wants_remember(data, default):
    """'이 PC 기억하기' 체크: 숨은 remember=0 뒤에 체크하면 remember=1이 붙는다. 값이 아예 없으면 default."""
    values = data.getlist("remember")
    return "1" in values if values else default


def remember_pc(request, response, user, remember):
    """login 뒤에 부른다. 기억하면 1년 기기 토큰 + 기본 세션, 아니면 이 PC 기기 토큰을 지우고 브라우저를 닫으면 끝나는 세션."""
    if remember:
        request.session.set_expiry(None)
        return set_device_cookie(request, response, user)
    old = request.COOKIES.get(DEVICE_COOKIE)
    if old:  # 이 PC에 남은 앞사람(또는 나)의 기기 토큰
        DeviceToken.objects.filter(token_hash=hash_token(old)).delete()
        response.delete_cookie(DEVICE_COOKIE)
    request.session.set_expiry(0)
    return response


def set_device_cookie(request, response, user):
    """이 PC를 user로 1년 기억한다. 같은 PC의 예전 토큰은 지우고 새로 발급(쌓이지 않게).
    손님을 처음 만든 세션이거나 이 PC의 예전 토큰이 '처음 PC'였으면 새 토큰에도 그 표시(origin)를 잇는다(S5)."""
    old = request.COOKIES.get(DEVICE_COOKIE)
    origin = request.session.get(GUEST_ORIGIN) == user.pk
    if old:
        olds = DeviceToken.objects.filter(token_hash=hash_token(old))
        origin = origin or olds.filter(user=user, origin=True).exists()
        olds.delete()
    response.set_cookie(
        DEVICE_COOKIE,
        DeviceToken.issue(user, origin=origin),
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
