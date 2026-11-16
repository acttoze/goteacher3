from accounts.models import is_account_member


def can_feedback(request):
    """V6 머리줄 '의견 보내기'(feedback/_shell.html): 계정 회원만(손님 제외)."""
    user = getattr(request, "user", None)
    return {"can_feedback": user is not None and is_account_member(user)}
