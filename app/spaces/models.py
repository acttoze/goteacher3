"""School · Space(동학년) · Member. 공간 소속 모델은 SpaceQuerySet.in_space()로만 조회한다."""
import secrets

from django.conf import settings
from django.db import models
from django.shortcuts import get_object_or_404


def new_token():
    return secrets.token_urlsafe(24)


class SpaceQuerySet(models.QuerySet):
    def in_space(self, space):
        if space is None or space.pk is None:
            raise ValueError("공간 없이 공간 소속 데이터를 조회할 수 없습니다.")
        return self.filter(space=space)


def get_in_space_or_404(model, request, **lookup):
    """request.space(미들웨어가 정함) 안에서만 찾는다. 다른 공간이면 404."""
    return get_object_or_404(model.objects.in_space(request.space), **lookup)


class School(models.Model):
    neis_atpt = models.CharField("시도교육청 코드", max_length=10)
    neis_code = models.CharField("학교 코드", max_length=10)
    name = models.CharField("학교 이름", max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["neis_atpt", "neis_code"], name="uniq_school_neis"),
        ]

    def __str__(self):
        return self.name


class Space(models.Model):
    """동학년 공간.

    token: 공간 주소(/s/<token>/). 들어오려면 소속 회원이어야 한다.
    invite_token: 초대 링크(B7). 주소와 따로 두어, 링크가 새면 초대만 새로 발급한다.
    """

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="spaces")
    grade = models.PositiveSmallIntegerField("학년")
    name = models.CharField("이름", max_length=100)
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    invite_token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.school} {self.name}"


class Member(models.Model):
    """이름만 고르고 들어온 사람도 Member.

    user: 구글 회원(google:<sub>) 또는 초대 링크로 이름만 들어온 손님(guest:<토큰>, accounts.is_guest).
    손님도 User라서 기기 토큰·소속 확인 미들웨어를 그대로 쓴다. 구글을 이으면 absorb_guest로 합친다.
    """

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="members"
    )
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    name = models.CharField("이름", max_length=40)
    cls = models.CharField("반", max_length=20, help_text="예: 4-1, 4-전담")
    position = models.CharField("직위", max_length=40, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["space", "user"], name="uniq_member_space_user"),
        ]

    def __str__(self):
        return f"{self.name}({self.cls})"


def absorb_guest(guest, user):
    """손님(guest)의 회원 자리를 user로 옮기고 손님을 지운다. 같은 공간에 둘 다 있으면 user 쪽을 남긴다.

    손님의 기기 토큰·세션은 옮기지 않는다(같은 반·이름으로 들어온 다른 PC가 구글 계정을 얻지 않게).
    손님 User를 지우면 그 기기 토큰(CASCADE)과 세션(사용자 없음)이 함께 끊긴다.
    """
    Member.objects.filter(user=guest).exclude(space__members__user=user).update(user=user)
    Member.objects.filter(user=guest).delete()
    guest.delete()
