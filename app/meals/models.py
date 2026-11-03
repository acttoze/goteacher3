"""M1 급식 설정·식판 사진. 조회는 SpaceQuerySet.in_space()로만.

- MealPhoto.photo: 서버가 재인코딩한 JPEG만(repairs.photo). 경로에 올린 이름을 쓰지 않는다. 내려받기는 회원 확인 뷰로만(TV 공개 경로는 M2).
- stored는 한글 파일·공지 첨부·수리 사진과 같은 한도(SourceFile.used_bytes). 행이 지워지면 파일도 지운다(hwpfiles.retention).
- 보관: 그 급식 날짜부터 30일 뒤 삭제(hwpfiles.retention).
"""
import hashlib

from django.db import models
from django.utils import timezone

from spaces.models import Space, SpaceQuerySet, new_token

from . import content


def _photo_path(instance, filename):
    """올린 파일 이름은 경로에 쓰지 않는다. 사진을 바꾸면 새 이름(옛 파일은 커밋 뒤 지운다)."""
    return f"meal/{instance.space_id}/{new_token()}.jpg"


class MealSettings(models.Model):
    space = models.OneToOneField(Space, on_delete=models.CASCADE, related_name="meal_settings")
    screens = models.JSONField("TV 화면 순서·켜고 끄기", default=list)  # [{"key": "menu", "on": true}, ...]

    objects = SpaceQuerySet.as_manager()

    @classmethod
    def screens_of(cls, space):
        """이 학교 TV 화면 목록(content.screens). 아직 저장한 적이 없으면 처음 상태."""
        saved = cls.objects.in_space(space).values_list("screens", flat=True).first()
        return content.screens(saved or [])


class MealPhoto(models.Model):
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="meal_photos")
    date = models.DateField("급식 날짜")
    photo = models.FileField(upload_to=_photo_path, blank=True)
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(바이트)", default=0)
    by = models.ForeignKey("spaces.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField("올린 때(바꾸면 새 시각)", default=timezone.now)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["space", "date"], name="uniq_meal_photo_per_day")]

    def __str__(self):
        return f"{self.date}"

    @property
    def version(self):
        """사진 주소의 ?v=. 바꿀 때마다 저장 이름이 달라지므로 같은 초에 바꿔도 브라우저 캐시에 옛 사진이 남지 않는다. 저장 경로는 주소에 드러내지 않는다."""
        return hashlib.sha256(self.photo.name.encode()).hexdigest()[:12]
