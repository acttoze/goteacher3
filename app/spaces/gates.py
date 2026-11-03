"""학교 공간 게이트(H2). SpaceMiddleware가 request.space·request.member를 정한 뒤에 돈다."""
from functools import wraps

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
    안내 한 줄은 shell.html. 모으기·오늘·일정·설정에는 걸지 않는다(리드 결정: 기존 회원은 계속 쓴다)."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        if write and request.space.is_school and not request.space.school.writable:
            return HttpResponseForbidden(EXPIRED)
        return view(request, *args, **kwargs)
    return inner
