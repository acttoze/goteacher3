"""Q10 커뮤니티(33번). 공간에 속한 구글 회원만 보고 쓰며, 화면에는 닉네임만 나온다.

- 공간에 속하지 않아 공간 필터가 없다. 들어오는 길은 게이트(community_view) 하나다: 비로그인은 로그인으로, 손님·공간 없는 회원은 안내(403), 닉네임 없는 회원은 입장 화면.
- 틀은 양식 자료실과 같다(library._frame). 안내 화면만 bare. 응답은 저장 금지·검색 제외.
- 운영자가 차단한 회원은 읽기만 된다(writer). 자기 글·댓글 지우기는 차단·내림과 상관없이 된다.
- 응답에 쓴 사람의 이름·메일·학교·공간을 넣지 않는다. 템플릿에는 닉네임(.by)만 넘어가고, 내 글인지는 author_id를 서버에서 비교한다.
- 입력은 공지와 같은 _clean. 전화번호·주민번호 모양은 거부한다(입력값은 그대로 돌려주고 로그에 남기지 않는다). 학생 정보는 값으로 찾는 규칙이 없어(collect는 칸 이름만 본다) 안내만 한다.
"""
import re
import unicodedata
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from accounts.models import is_guest
from collect.paste import clean_text
from config.ratelimit import TOO_MANY, allow
from library.views import _frame
from notices.views import _clean
from partners.views import PHONE
from spaces.models import Member

from .models import BODY_MAX, COMMENT_MAX, NICK_MAX, NICK_MIN, NICK_RE, TITLE_MAX, Comment, CommunityProfile, Post, Report, clean_nickname, nickname_key, remove

HOME = "/community"
NICK_PATH = "/community/nick"
PER_PAGE = 20
HOUR = 3600
NICK_PER_HOUR = 20
POST_PER_HOUR = 10
COMMENT_PER_HOUR = 30
REPORT_PER_HOUR = 20
NICK_EVERY = timedelta(days=1)
NICK_BANNED = ("admin", "관리", "운영", "고티처", "goteacher", "나간선생님")
RRN = re.compile(r"(?<!\d)\d{6}\s*[-–—]\s*\d{7}(?!\d)")
PRIVATE_REFUSED = "전화번호나 주민등록번호로 보이는 숫자가 있어요. 빼고 다시 올려 주세요."
TOAST = "community_toast"


def _private_number(text):
    """전화번호·주민번호 모양인가. 값은 오류 문구·로그에 남기지 않는다."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")
    return bool(PHONE.search(plain) or RRN.search(plain))


def _page(request, template, ctx=None, status=200):
    profile = getattr(request, "profile", None)
    return render(request, template, {**_frame(request, page="community"), "blocked": bool(profile and profile.blocked_at), **(ctx or {})}, status=status)


def _nick_page(request, status=200, **ctx):
    profile = request.profile
    ready = profile.nickname_changed_at + NICK_EVERY if profile else None
    return _page(request, "community/nick.html", {
        "profile": profile, "nick_min": NICK_MIN, "nick_max": NICK_MAX,
        "ready_at": ready if ready and ready > timezone.now() else None, **ctx}, status)


def community_view(view):
    """모든 커뮤니티 주소의 게이트. 닉네임 없는 회원은 어느 주소든 입장 화면을 받는다(저장되지 않는다)."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.user.is_authenticated:
            r = redirect("/start?" + urlencode({"next": HOME}, safe="/"))
        elif is_guest(request.user):
            r = _page(request, "community/guest.html", {"login_href": "/auth/google/login?" + urlencode({"next": HOME}, safe="/")}, 403)
        elif not Member.objects.filter(user=request.user).exists():
            r = _page(request, "community/no_space.html", status=403)
        else:
            request.profile = CommunityProfile.objects.filter(user=request.user).first()
            r = _nick_page(request) if request.profile is None and request.path != NICK_PATH else view(request, *args, **kwargs)
        r["X-Robots-Tag"] = "noindex, nofollow"
        add_never_cache_headers(r)
        return r
    return inner


def writer(view):
    """보내기 게이트: 차단한 회원의 글·댓글·신고·닉네임 바꾸기는 403. community_view 안쪽에 둔다."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if request.method == "POST" and request.profile and request.profile.blocked_at:
            return _page(request, "community/blocked.html", status=403)
        return view(request, *args, **kwargs)
    return inner


def _see_other(to):
    r = redirect(to)
    r.status_code = 303
    return r


def _toast(request, text):
    request.session[TOAST] = text


# ---------------------------------------------------------------- 닉네임
def _nick_error(nickname):
    if not NICK_MIN <= len(nickname) <= NICK_MAX:
        return f"닉네임은 {NICK_MIN}자에서 {NICK_MAX}자까지 써 주세요."
    if not NICK_RE.fullmatch(nickname):
        return "닉네임은 한글·영문·숫자로 쓰고, 공백은 가운데 한 칸까지 쓸 수 있어요."
    key = nickname_key(nickname)
    if any(word in key for word in NICK_BANNED):
        return "관리자·운영자·고티처로 보이는 닉네임은 쓸 수 없어요."
    return ""


TAKEN = "이미 쓰는 닉네임이에요. 다른 닉네임을 골라 주세요."


@community_view
@require_http_methods(["GET", "HEAD", "POST"])
@writer
def nick(request):
    """처음 정하기와 바꾸기(하루 1번). 겹침은 DB 유일 제약이 막는다."""
    if request.method != "POST":
        return _nick_page(request)
    who, profile = str(request.user.pk), request.profile
    raw = request.POST.get("nickname", "")[:200]
    if not allow("community-nick", who, NICK_PER_HOUR, HOUR):  # 거부된 시도도 센다(닉네임을 맞춰 보지 못하게)
        return _nick_page(request, 429, error=TOO_MANY, value=raw)
    nickname = clean_nickname(raw)
    error = _nick_error(nickname)
    if error:
        return _nick_page(request, 400, error=error, value=nickname)
    now = timezone.now()
    if profile and profile.nickname_changed_at > now - NICK_EVERY:
        return _nick_page(request, 400, error="닉네임은 하루에 한 번만 바꿀 수 있어요.", value=nickname)
    key = nickname_key(nickname)
    try:
        with transaction.atomic():
            if profile is None:
                CommunityProfile.objects.create(user=request.user, nickname=nickname, nickname_key=key, nickname_changed_at=now)
            elif not CommunityProfile.objects.filter(pk=profile.pk, nickname_changed_at__lte=now - NICK_EVERY).update(
                    nickname=nickname, nickname_key=key, nickname_changed_at=now):  # 겹친 요청은 한 번만
                return _nick_page(request, 400, error="닉네임은 하루에 한 번만 바꿀 수 있어요.", value=nickname)
    except IntegrityError:
        if profile is None and CommunityProfile.objects.filter(user=request.user).exists():  # 같은 사람이 두 번 눌렀다
            return _see_other(HOME)
        return _nick_page(request, 400, error=TAKEN, value=nickname)
    return _see_other(HOME)


# ---------------------------------------------------------------- 목록·글쓰기
def _visible_posts():
    return Post.objects.filter(hidden_at__isnull=True)


def _my_hidden(user):
    """운영자가 내린 내 글·댓글(내린 글에 단 댓글 포함). 주소는 404라 지우기는 목록 위 칸에서 한다."""
    return {"hidden_posts": Post.objects.filter(author=user, hidden_at__isnull=False),
            "hidden_comments": Comment.objects.filter(Q(hidden_at__isnull=False) | Q(post__hidden_at__isnull=False), author=user)}


def _list_page(request, status=200, **ctx):
    posts = (_visible_posts().select_related("author__community_profile")
             .annotate(n_comments=Count("comments", filter=Q(comments__hidden_at__isnull=True)))
             .order_by("-created_at", "-pk"))  # 집계하면 Meta.ordering이 빠진다
    page = Paginator(posts, PER_PAGE).get_page(request.GET.get("page"))
    return _page(request, "community/index.html", {
        "posts": page, "profile": request.profile, "toast": request.session.pop(TOAST, ""),
        "title_max": TITLE_MAX, "body_max": BODY_MAX, "form": {"title": "", "body": ""}, **_my_hidden(request.user), **ctx}, status)


@community_view
@require_safe
def index(request):
    return _list_page(request)


@community_view
@require_http_methods(["GET", "HEAD", "POST"])
@writer
def new(request):
    """GET은 대화상자가 열린 목록. 오류면 입력값을 그대로 둔 채 대화상자를 연다."""
    if request.method != "POST":
        return _list_page(request, open_form=True)
    form = {"title": clean_text(_clean(request.POST, "title")), "body": _clean(request.POST, "body")}
    errors = {}
    if not form["title"]:
        errors["title"] = "제목을 써 주세요."
    elif len(form["title"]) > TITLE_MAX:
        errors["title"] = f"제목은 {TITLE_MAX}자까지 쓸 수 있어요."
    elif _private_number(form["title"]):
        errors["title"] = PRIVATE_REFUSED
    if not form["body"]:
        errors["body"] = "본문을 써 주세요."
    elif len(form["body"]) > BODY_MAX:
        errors["body"] = f"본문은 {BODY_MAX}자까지 쓸 수 있어요."
    elif _private_number(form["body"]):
        errors["body"] = PRIVATE_REFUSED
    if errors:
        return _list_page(request, 400, open_form=True, form=form, errors=errors)
    if not allow("community-post", str(request.user.pk), POST_PER_HOUR, HOUR):
        return _list_page(request, 429, open_form=True, form=form, errors={"body": TOO_MANY})
    post = Post.objects.create(author=request.user, **form)
    return _see_other(f"{HOME}/{post.key}")


# ---------------------------------------------------------------- 상세·댓글
def _post_or_404(key):
    return get_object_or_404(_visible_posts().select_related("author__community_profile"), key=key)


def _visible_comment_or_404(key):
    """내려진 댓글, 내려진 글의 댓글은 주소째 없다."""
    return get_object_or_404(Comment.objects.select_related("post"), key=key, hidden_at__isnull=True, post__hidden_at__isnull=True)


def _detail_page(request, post, status=200, **ctx):
    comments = list(post.comments.filter(hidden_at__isnull=True).select_related("author__community_profile"))
    mine = Report.objects.filter(reporter=request.user)
    reported = set(mine.filter(kind=Report.Kind.COMMENT, key__in=[c.key for c in comments]).values_list("key", flat=True))
    for c in comments:
        c.mine, c.reported = c.author_id == request.user.pk, c.key in reported
    return _page(request, "community/detail.html", {
        "p": post, "mine": post.author_id == request.user.pk, "comments": comments, "comment_max": COMMENT_MAX,
        "reported": mine.filter(kind=Report.Kind.POST, key=post.key).exists(), "reasons": Report.Reason.choices,
        "toast": request.session.pop(TOAST, ""), "comment_text": "", **ctx}, status)


@community_view
@require_safe
def detail(request, key):
    return _detail_page(request, _post_or_404(key))


@community_view
@require_POST
@writer
def comment(request, key):
    post = _post_or_404(key)
    text = _clean(request.POST, "body")
    error = "댓글을 써 주세요." if not text else f"댓글은 {COMMENT_MAX}자까지 쓸 수 있어요." if len(text) > COMMENT_MAX else PRIVATE_REFUSED if _private_number(text) else ""
    if error:
        return _detail_page(request, post, 400, comment_text=text, comment_error=error)
    if not allow("community-comment", str(request.user.pk), COMMENT_PER_HOUR, HOUR):
        return _detail_page(request, post, 429, comment_text=text, comment_error=TOO_MANY)
    Comment.objects.create(post=post, author=request.user, body=text)
    return _see_other(f"{HOME}/{post.key}")


# ---------------------------------------------------------------- 지우기(본인만, 실제 삭제. 운영자가 내렸어도 된다)
@community_view
@require_POST
def post_delete(request, key):
    mine = Post.objects.filter(key=key, author=request.user)
    get_object_or_404(mine)
    remove(mine, Comment.objects.none())
    _toast(request, "글을 지웠어요.")
    return _see_other(HOME)


@community_view
@require_POST
def comment_delete(request, key):
    mine = Comment.objects.filter(key=key, author=request.user)
    c = get_object_or_404(mine.select_related("post"))
    remove(Post.objects.none(), mine)
    _toast(request, "댓글을 지웠어요.")
    return _see_other(HOME if c.post.hidden_at else f"{HOME}/{c.post.key}")


# ---------------------------------------------------------------- 신고
def _report(request, kind, target, post):
    if target.author_id == request.user.pk:  # 내 글은 신고 대상이 아니다
        raise Http404
    if not allow("community-report", str(request.user.pk), REPORT_PER_HOUR, HOUR):
        return _detail_page(request, post, 429, report_error=TOO_MANY)
    reason = request.POST.get("reason", "")
    reason = reason if reason in Report.Reason.values else ""
    Report.objects.get_or_create(kind=kind, key=target.key, reporter=request.user, defaults={"reason": reason})  # 같은 사람은 한 번만
    _toast(request, "신고했어요. 운영자가 확인할게요.")
    return _see_other(f"{HOME}/{post.key}")


@community_view
@require_POST
@writer
def post_report(request, key):
    post = _post_or_404(key)
    return _report(request, Report.Kind.POST, post, post)


@community_view
@require_POST
@writer
def comment_report(request, key):
    c = _visible_comment_or_404(key)
    return _report(request, Report.Kind.COMMENT, c, _post_or_404(c.post.key))
