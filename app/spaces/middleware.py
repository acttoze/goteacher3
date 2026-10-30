from django.shortcuts import get_object_or_404

from .models import Space


class SpaceMiddleware:
    """URL에 <space_token>이 있으면 공간을 찾아 request.space에 둔다. 없는 토큰이면 404.

    space_token은 뷰 인자에서 빼므로, 공간 주소의 뷰는 request.space만 쓴다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.space = None
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        token = view_kwargs.pop("space_token", None)
        if token is not None:
            request.space = get_object_or_404(Space, invite_token=token)
        return None
