"""School · Space(동학년) · Member. 공간 소속 모델은 SpaceQuerySet.in_space()로만 조회한다."""
import secrets

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
    """동학년 공간. 외부 주소는 invite_token으로만 가리킨다."""

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="spaces")
    grade = models.PositiveSmallIntegerField("학년")
    name = models.CharField("이름", max_length=100)
    invite_token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.school} {self.name}"


class Member(models.Model):
    """이름만 고르고 들어온 사람도 Member."""

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="members")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    name = models.CharField("이름", max_length=40)
    cls = models.CharField("반", max_length=20, help_text="예: 4-1, 4-전담")
    position = models.CharField("직위", max_length=40, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return f"{self.name}({self.cls})"
