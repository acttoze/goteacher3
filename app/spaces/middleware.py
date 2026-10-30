from urllib.parse import urlencode

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from .models import Member, Space


class SpaceMiddleware:
    """URL에 <space_token>이 있으면 공간과 내 회원 정보를 request.space·request.member에 둔다.

    - 익명이면 토큰을 찾아보기 전에 로그인 화면으로(토큰 존재 여부를 알려 주지 않는다).
    - 없는 토큰이거나 그 공간 회원이 아니면 404.
    space_token은 뷰 인자에서 빼므로, 공간 주소의 뷰는 request.space만 쓴다.
    초대(B7: /j/<invite_token> → /join)·받는 사람 제출(C5)은 /s/ 밖의 자기 토큰 주소를 쓴다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
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
        member = Member.objects.in_space(space).filter(user=request.user).first()
        if member is None:
            raise Http404
        request.space, request.member = space, member
        return None
