"""G4 양식 자료실(FormDoc). 운영자가 관리 명령 import_forms로만 올리고, 누구나 로그인 없이 보고 내려받는다.

- 공간에 속하지 않는 공개 자료라 공간 필터 대상이 아니다(학교 검색 결과처럼 서비스 공용).
- 외부 주소는 slug만 쓴다(읽기 쉬운 한글 slug, 겹치면 -2·-3). 원본 id(src)·DB 순번은 주소에 쓰지 않는다.
- 파일은 default storage(MEDIA_ROOT 볼륨)의 forms/<src>.<형식>. MEDIA_URL로 서빙하지 않고 내려받기 뷰로만 준다.
- 조회·내려받기 수는 단순히 센다(봇 User-Agent·HEAD는 빼고).
"""
from django.db import models
from django.utils.text import slugify

CATEGORIES = ["학교", "교육청"]  # prototype 자료실 분류 순서


def _file_path(instance, filename):
    """원래 파일 이름은 경로에 쓰지 않는다."""
    return f"forms/{instance.src}.{instance.filename.rsplit('.', 1)[-1]}"


def make_slug(title, taken):
    base = slugify(title.replace("·", " "), allow_unicode=True)[:80].strip("-") or "form"
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"
    return slug


class FormDoc(models.Model):
    src = models.CharField("가져온 원본 id", max_length=20, unique=True)
    slug = models.SlugField(max_length=100, unique=True, allow_unicode=True)
    category = models.CharField("분류", max_length=10, choices=[(c, c) for c in CATEGORIES])
    title = models.CharField("제목", max_length=200)
    desc = models.TextField("설명")
    tips = models.JSONField("이렇게 쓰세요", default=list)
    file = models.FileField(upload_to=_file_path)
    filename = models.CharField("내려받을 파일 이름", max_length=200)
    size = models.PositiveIntegerField("크기(바이트)")
    downloads = models.PositiveIntegerField("내려받기 수", default=0)
    views = models.PositiveIntegerField("조회 수", default=0)
    created_at = models.DateTimeField("등록 시각", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-src"]

    def __str__(self):
        return self.title

    @property
    def ext(self):
        return self.filename.rsplit(".", 1)[-1].lower()

    @property
    def kb(self):
        return max(1, round(self.size / 1024))
