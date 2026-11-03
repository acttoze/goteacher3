"""I1 공지(prototype board·noticeDetail, TR.seedNotices). 조회는 SpaceQuerySet.in_space()로만.

- 판(NoticeVersion): fields에 제목·항목·받는 곳·첨부 이름을 함께 적어 어떤 판이든 그때 모습대로 다시 그린다.
  I2의 실질 변경은 ITEMS만 비교하면 된다.
- 받는 곳(audience): 'all' 또는 ',g4,office,'. Member.group은 본인이 고치는 값이라 보기 범위에만 쓴다(권한은 by·is_manager).
- 첨부(NoticeFile)는 공지 단위. 옛 파일은 보관하지 않아 옛 판에는 이름만 남는다. 저장 경로에 올린 이름을 쓰지 않고, 내려받기는 회원 확인 뷰로만.
  저장량은 한글 파일과 같은 한도(SourceFile.used_bytes), 행이 지워지면 디스크 파일도 지운다(hwpfiles.retention).
"""
from django.db import models
from django.db.models import Q

from spaces.models import Member, Space, SpaceQuerySet, new_token

ALL = "all"
TITLE, AUDIENCE, FILES = "제목", "받는 곳", "첨부"
ITEMS = ("일시", "장소", "대상", "준비물")
ORDER = (TITLE, *ITEMS, AUDIENCE, FILES)
FILE_LIST = "첨부 목록"  # fields 안 [{key, name}]: 같은 이름으로 바꾼 것도 알아보고, 옛 판의 첨부를 그린다. key는 화면에 안 쓴다


def audience_value(groups):
    """고른 그룹 → 저장 값. 쉼표로 감싸 'g1'이 다른 값 안에서 맞는 일이 없게 한다."""
    return f",{','.join(groups)}," if groups else ALL


def audience_text(value):
    if value == ALL:
        return "전체"
    labels = dict(Member.Group.choices)
    return "·".join(labels.get(g, g) for g in value.split(",") if g)


class NoticeQuerySet(SpaceQuerySet):
    def visible_to(self, member):
        """내게 보이는 공지: 대상에 내 그룹이 있거나, 내가 썼거나, 관리자."""
        if member.is_manager:
            return self
        seen = Q(audience=ALL) | Q(by=member)
        if member.group:
            seen |= Q(audience__contains=f",{member.group},")
        return self.filter(seen)


class Notice(models.Model):
    """by: 고치기·지우기 권한의 근거(나가면 비고 관리자만 고친다). by_name·group은 쓸 때의 표시 값(나간 뒤에도 읽히게)."""

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="notices")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    title = models.CharField("제목", max_length=200)
    by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="notices")
    by_name = models.CharField("쓴 사람", max_length=40)
    group = models.CharField("쓴 사람 소속", max_length=10, choices=Member.Group.choices, blank=True, default="")
    audience = models.CharField("받는 곳", max_length=120, default=ALL)
    must = models.BooleanField("확인 필수", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = NoticeQuerySet.as_manager()

    def __str__(self):
        return self.key

    @property
    def audience_text(self):
        return audience_text(self.audience)


class NoticeVersionQuerySet(SpaceQuerySet):
    space_lookup = "notice__space"


class NoticeVersion(models.Model):
    notice = models.ForeignKey(Notice, on_delete=models.CASCADE, related_name="versions")
    v = models.PositiveIntegerField("판")
    fields = models.JSONField("그 판의 제목·항목·받는 곳·첨부 이름", default=dict)
    body = models.TextField("본문", blank=True)
    by = models.CharField("고친 사람", max_length=40)
    at = models.DateTimeField(auto_now_add=True)

    objects = NoticeVersionQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["notice", "v"], name="uniq_notice_version")]

    def __str__(self):
        return f"v{self.v}"


def _file_path(instance, filename):
    """올린 파일 이름은 경로에 쓰지 않는다."""
    return f"notice/{instance.notice.space_id}/{instance.key}.{instance.kind}"


class NoticeFileQuerySet(SpaceQuerySet):
    space_lookup = "notice__space"


class NoticeFile(models.Model):
    notice = models.ForeignKey(Notice, on_delete=models.CASCADE, related_name="files")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    name = models.CharField("올린 파일 이름", max_length=200)
    kind = models.CharField("판별 형식(매직 바이트)", max_length=4)
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(바이트)")
    file = models.FileField(upload_to=_file_path)
    at = models.DateTimeField(auto_now_add=True)

    objects = NoticeFileQuerySet.as_manager()

    def __str__(self):
        return self.key
