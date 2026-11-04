"""N1 협력업체. 업체는 운영자만 등록·공개하고, 교사 추천(Suggestion)은 운영자 확인을 기다릴 뿐 공개되지 않는다.

- 공간에 속하지 않는 공개 자료라 공간 필터 대상이 아니다(양식 자료실과 같음).
- 외부 주소는 slug만 쓴다(읽기 쉬운 한글 slug, 겹치면 -2·-3). 업체를 시드로 넣지 않는다(리드 결정: 가상 업체 금지).
"""
from urllib.parse import urlsplit

from django.core.validators import URLValidator
from django.db import models

from library.models import make_slug

CATS = ["에듀테크", "졸업앨범·사진", "수학여행·수련활동", "현장체험학습", "전세버스", "찾아가는 교육·체험", "공연·행사", "청소·방역 용역", "인쇄·현수막", "교구·기자재"]  # prototype TR.vendorCats
RESERVED_SLUGS = {"suggest"}  # /partners/suggest
_CAT_CHOICES = [(c, c) for c in CATS]


class Partner(models.Model):
    slug = models.SlugField(max_length=100, unique=True, allow_unicode=True, editable=False)
    name = models.CharField("이름", max_length=100)
    cat = models.CharField("분류", max_length=20, choices=_CAT_CHOICES)
    area = models.CharField("지역", max_length=40, help_text="예: 전국, 수도권, 서울, 경기 · 인천, 대전")
    desc = models.TextField("소개")
    phone = models.CharField("연락처", max_length=30, blank=True)
    url = models.URLField("누리집", max_length=300, blank=True, validators=[URLValidator(schemes=["http", "https"])])
    tags = models.CharField("태그", max_length=200, blank=True, help_text="쉼표로 나눕니다")
    is_public = models.BooleanField("공개", default=False)
    created_at = models.DateTimeField("등록 시각", auto_now_add=True)

    class Meta:
        ordering = ["name", "slug"]
        verbose_name = verbose_name_plural = "협력업체"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            taken = RESERVED_SLUGS | set(Partner.objects.values_list("slug", flat=True))
            self.slug = make_slug(self.name, taken)
        super().save(*args, **kwargs)

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def link(self):
        """링크로 걸어도 되는 누리집. 명령을 거치지 않고 넣은 값도 http(s)만 링크가 된다."""
        return self.url if urlsplit(self.url).scheme in ("http", "https") else ""


class Suggestion(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "확인 대기"
        ACCEPTED = "accepted", "올리기로 함"
        REJECTED = "rejected", "안 올림"

    name = models.CharField("업체 이름", max_length=100)
    cat = models.CharField("분류", max_length=20, choices=_CAT_CHOICES)
    contact = models.CharField("연락처", max_length=100)
    note = models.CharField("한 줄", max_length=200, blank=True)
    by_member = models.ForeignKey("spaces.Member", verbose_name="추천한 회원", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    by_name = models.CharField("추천한 사람", max_length=150)
    school = models.CharField("학교", max_length=100, blank=True)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField("추천 시각", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = verbose_name_plural = "업체 추천"

    def __str__(self):
        return self.name
