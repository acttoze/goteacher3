"""H4 취합 접근 제한(보안 검토 반영). 학교 공간에서 합본·내보내기·한글 파일은 만든 사람과 관리자만, 그 외 회원은 404(존재 숨김).

동학년 공간은 지금처럼 회원 모두. 그룹·직책 글자는 보지 않는다. 받는 링크(/c/<link_token>)는 이 경로를 타지 않는다.
"""
from functools import wraps

from django.http import Http404

from hwpfiles.models import ResultFile
from spaces.models import get_in_space_or_404


def can_open(request, owner_id):
    return not request.space.is_school or request.member.is_manager or owner_id == request.member.pk


def owner_view(model):
    """주소의 key로 이 공간 안에서 찾아 view(request, obj)로 넘긴다. 없거나 열 수 없으면 404.
    채운 결과 파일은 취합 주인 기준(관리자가 만들어도 만든 선생님이 받는다)."""
    def deco(view):
        @wraps(view)
        def inner(request, key, *args, **kwargs):
            obj = get_in_space_or_404(model, request, key=key)
            owner = obj.collection.created_by_id if model is ResultFile else obj.created_by_id
            if not can_open(request, owner):
                raise Http404
            return view(request, obj, *args, **kwargs)
        return inner
    return deco
