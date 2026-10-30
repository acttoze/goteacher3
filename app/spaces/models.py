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
    """이름만 고르고 들어온 사람도 Member. user는 구글로 로그인한 회원만(27번 4-1의 oauth_id)."""

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
