from urllib.parse import urlencode

from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect

from .models import Member, Space


def _forward(request, old):
    """옛 동학년 주소 → 학교 공간 같은 경로. 보기(GET)만 넘긴다. 옛 탭의 쓰기는 실행하지 않고 새 화면으로 보낸다."""
    new = old.merged_into
    if not Member.objects.in_space(new).filter(user=request.user).exists():
        raise Http404
    if request.headers.get("HX-Request"):  # 옛 탭의 폴링·부분 갱신: 조각을 끼우지 않고 페이지를 다시 열어 새 주소로
        r = HttpResponse(status=204)
        r["HX-Refresh"] = "true"
        return r
    if request.method in ("GET", "HEAD"):
        return redirect(request.get_full_path().replace(f"/s/{old.token}/", f"/s/{new.token}/", 1))
    r = HttpResponseRedirect(f"/s/{new.token}/today")
    r.status_code = 303
    return r


class SpaceMiddleware:
    """URL에 <space_token>이 있으면 공간과 내 회원 정보를 request.space·request.member에 둔다.

    - 익명이면 토큰을 찾아보기 전에 로그인 화면으로(토큰 존재 여부를 알려 주지 않는다).
    - 없는 토큰이거나 그 공간 회원이 아니면 404.
    space_token은 뷰 인자에서 빼므로, 공간 주소의 뷰는 request.space만 쓴다.
    - 학교 공간으로 합쳐진 옛 공간(H1)이면 새 공간 회원에게만 넘겨 준다(아니면 404).
    초대(B7: /j/<invite_token> → /join)·받는 사람 제출(C5)은 /s/ 밖의 자기 토큰 주소를 쓴다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if "\x00" in request.path:  # 주소의 토큰·키는 모두 DB로 간다. NUL은 PostgreSQL이 거부해 500이 되므로 여기서 끊는다(H4)
            raise Http404
        request.space = None
        request.member = None
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        token = view_kwargs.pop("space_token", None)
        if token is None:
            return None
        if not request.user.is_authenticated:
            if request.headers.get("HX-Request"):  # 폴링 중 로그아웃: 카드 안에 로그인 화면을 넣지 않고 화면 전체를 옮긴다
                r = HttpResponse(status=204)
                r["HX-Redirect"] = "/"
                return r
            return redirect("/start?" + urlencode({"next": request.get_full_path()}, safe="/"))
        space = get_object_or_404(Space, token=token)
        if space.merged_into_id:
            return _forward(request, space)
        member = Member.objects.in_space(space).filter(user=request.user).first()
        if member is None:
            raise Http404
        request.space, request.member = space, member
        return None
