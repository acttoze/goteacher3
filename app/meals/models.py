"""M1 급식 설정·식판 사진. 조회는 SpaceQuerySet.in_space()로만.

- MealPhoto.photo: 서버가 재인코딩한 JPEG만(repairs.photo). 경로에 올린 이름을 쓰지 않는다. 내려받기는 회원 확인 뷰, 또는 TV 토큰으로 그 학교 오늘 사진만(meals.tv).
- MealSettings.tv_token: TV 주소(/tv/<token>). 로그인이 없는 교실 밖 화면이라 입력 화면에서 복사해야 하므로 원문으로 둔다. 재발급하면 옛 주소는 바로 끊긴다.
- stored는 한글 파일·공지 첨부·수리 사진과 같은 한도(SourceFile.used_bytes). 행이 지워지면 파일도 지운다(hwpfiles.retention).
- 보관: 그 급식 날짜부터 30일 뒤 삭제(hwpfiles.retention).
- PhotoLink: 폰 QR 토큰(M3). 단추를 누를 때만 만들고 해시만 저장한다. 그날 자정과 2시간 중 빠른 쪽에 끝나고, 첫 업로드 뒤에는 10분 안으로 줄어든다(다시 찍기만).
  만든 회원이 권한을 잃거나 그날 사진을 지우면 바로 끊긴다. 지난 행은 hwpfiles.retention.
- MealPhoto.source·link_ref·by_name: 어느 경로로 올렸는지. 토큰 행이 지워지고 만든 사람이 나가도 기록이 남도록 FK가 아닌 값으로 둔다. 입력 화면에만 보인다.
"""
import hashlib
import secrets
from datetime import datetime, time, timedelta

from django.db import models, transaction
from django.utils import timezone

from accounts.models import hash_token
from spaces.models import Member, Space, SpaceQuerySet, new_token

from . import content


def _photo_path(instance, filename):
    """올린 파일 이름은 경로에 쓰지 않는다. 사진을 바꾸면 새 이름(옛 파일은 커밋 뒤 지운다)."""
    return f"meal/{instance.space_id}/{new_token()}.jpg"


MAX_LINKS = 10  # 회원 한 명당 살아 있는 QR 수. 새로 누를 때만 정리한다(탭 여러 개가 서로 끊지 않게 넉넉히)
MAX_UPLOADS = 5  # QR 하나로 올릴 수 있는 횟수(저장에 성공한 것만 센다)
LINK_AGE = timedelta(hours=2)
AFTER_UPLOAD = timedelta(minutes=10)  # 첫 업로드 뒤 남는 시간(다시 찍기만)


def next_midnight(now=None):
    return datetime.combine(timezone.localtime(now).date() + timedelta(days=1), time.min, tzinfo=timezone.get_current_timezone())


def new_tv_token():
    return secrets.token_urlsafe(32)


class MealSettings(models.Model):
    space = models.OneToOneField(Space, on_delete=models.CASCADE, related_name="meal_settings")
    tv_token = models.CharField("TV 주소 토큰", max_length=64, unique=True, default=new_tv_token)
    screens = models.JSONField("TV 화면 순서·켜고 끄기", default=list)  # [{"key": "menu", "on": true}, ...]
    theme = models.PositiveSmallIntegerField("TV 테마", default=1, choices=[(t["no"], t["name"]) for t in content.THEMES])

    objects = SpaceQuerySet.as_manager()

    @classmethod
    def screens_of(cls, space):
        """이 학교 TV 화면 목록(content.screens). 아직 저장한 적이 없으면 처음 상태."""
        saved = cls.objects.in_space(space).values_list("screens", flat=True).first()
        return content.screens(saved or [])


class MealPhoto(models.Model):
    class Source(models.TextChoices):
        PC = "pc", "PC"
        PHONE = "phone", "폰 QR"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="meal_photos")
    date = models.DateField("급식 날짜")
    photo = models.FileField(upload_to=_photo_path, blank=True)
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(바이트)", default=0)
    by = models.ForeignKey("spaces.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    by_name = models.CharField("올린 사람(폰이면 QR 만든 사람)", max_length=40, blank=True)
    source = models.CharField("올린 경로", max_length=5, choices=Source.choices, default=Source.PC)
    link_ref = models.BigIntegerField("폰 QR 행 번호(주소에 쓰지 않는다)", null=True, blank=True)
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


class PhotoLink(models.Model):
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="+")
    date = models.DateField("올릴 수 있는 날짜")
    token_hash = models.CharField(max_length=64, unique=True)
    by = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="+")
    uses = models.PositiveSmallIntegerField("올린 횟수", default=0)
    expires_at = models.DateTimeField()

    objects = SpaceQuerySet.as_manager()

    @classmethod
    def issue(cls, space, day, member):
        """새 QR. (행, 토큰 원문). 원문은 이 응답의 QR에만 쓰이고 저장되지 않는다."""
        raw, now = secrets.token_urlsafe(32), timezone.now()
        with transaction.atomic():
            mine = cls.objects.in_space(space).filter(by=member)
            mine.filter(expires_at__lte=now).delete()
            mine.filter(pk__in=list(mine.order_by("-pk").values_list("pk", flat=True)[MAX_LINKS - 1:])).delete()
            link = cls.objects.create(space=space, date=day, token_hash=hash_token(raw), by=member, expires_at=min(now + LINK_AGE, next_midnight(now)))
        return link, raw

    @property
    def is_open(self):
        """만료·날짜·횟수·학교 공간·만든 회원의 권한을 쓸 때마다 다시 본다."""
        by = self.by
        return (self.expires_at > timezone.now() and self.date == timezone.localdate() and self.uses < MAX_UPLOADS
                and self.space.is_school and by.space_id == self.space_id and (by.is_manager or by.can_meal))
