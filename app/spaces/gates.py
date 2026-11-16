"""학교 공간 게이트(H2). SpaceMiddleware가 request.space·request.member를 정한 뒤에 돈다."""
from functools import wraps

from django.conf import settings
from django.http import Http404, HttpResponseForbidden

EXPIRED = "학교 플랜 기간이 끝나 고칠 수 없어요. 읽기는 계속 돼요."


def school_required(view):
    """학교 공간이 아니면 404(동학년 공간에는 이 주소가 없는 것처럼)."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.space.is_school:
            raise Http404
        return view(request, *args, **kwargs)
    return inner


def no_org(view):
    """학교에만 있는 화면(급식·학교 화면·복무·정보수리방): 기관 공간이면 404(T1)."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if request.space.is_org:
            raise Http404
        return view(request, *args, **kwargs)
    return inner


def manager_required(view):
    """학교 관리자가 아니면 403. 직위 글자가 아니라 Member.is_manager로만 판단한다."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.member.is_manager:
            return HttpResponseForbidden()
        return view(request, *args, **kwargs)
    return inner


def school_write_required(view):
    """학교판 전용 기능의 쓰기: 학교 공간 플랜이 끝났으면(기한 지남 또는 plan=free) 거부, 읽기는 그대로. 동학년 공간은 통과.
    안내 한 줄은 shell.html. 취합·오늘·일정·설정에는 걸지 않는다(리드 결정: 기존 회원은 계속 쓴다)."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        if write and request.space.is_school and not request.space.school.writable:
            return HttpResponseForbidden(EXPIRED)
        return view(request, *args, **kwargs)
    return inner


LEGACY_LOCKED = "일정·복무·게시판 쓰기는 예전 고티처에서 해 주세요."


def legacy_locked(view):
    """V3 이관 학교의 일정·복무·게시판 쓰기 주소: 동시 사용 중(space.legacy_readonly)이면 관리자도 403. 쓰기 전용 주소에만 건다
    (쓰기 화면 GET도 막는다). 열려 있던 옛 탭의 htmx 쓰기는 gt.js가 X-GT-Legacy-RO를 보고 안내한다. 읽기·내려받기·공지 확인·취합 등 새 기능에는 걸지 않는다. 안내 한 줄은 spaces/_legacy_ro.html."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if request.space.legacy_readonly:
            r = HttpResponseForbidden(LEGACY_LOCKED, content_type="text/plain; charset=utf-8")
            r["X-GT-Legacy-RO"] = "1"  # gt.js가 이 표시가 있는 403에만 이 문구 + 예전 고티처 링크를 띄운다(다른 403과 구분)
            r["X-GT-Legacy-Site"] = settings.LEGACY_SITE_URL
            return r
        return view(request, *args, **kwargs)
    return inner
