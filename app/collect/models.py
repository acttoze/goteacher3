"""모으기: Collection ├ Field(입력 칸) ├ Row(받는 사람 줄) ─ Cell(줄×칸 값).

공간 격리: Collection은 space FK, 나머지는 collection을 거쳐 SpaceQuerySet.in_space()로만 조회한다.
주소에는 key(공간 안 화면)·link_token(받는 사람 링크)만 쓴다. 순번 ID는 쓰지 않는다.
제출 여부·시각·내는 사람은 Cell(by, at)에서 계산한다(지각 = at > due).
"""
from django.db import models
from django.shortcuts import get_object_or_404

from spaces.models import Member, Space, SpaceQuerySet, new_token


class Collection(models.Model):
    class Source(models.TextChoices):
        PASTE = "paste", "표 붙여넣기"
        SHEET = "sheet", "직접 입력"
        PRESET = "preset", "자주 쓰는 양식"
        HWP = "hwp", "한글 양식"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="collections")
    created_by = models.ForeignKey(
        Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="collections"
    )
    title = models.CharField("제목", max_length=200)
    source = models.CharField("가져온 방식", max_length=10, choices=Source.choices)
    due = models.DateTimeField("마감", null=True, blank=True)
    closed_at = models.DateTimeField("마감하고 잠근 때", null=True, blank=True)
    unit_head = models.JSONField(
        "받는 사람 칸 머리글", default=list, blank=True, help_text='예: ["학년반", "담임"]'
    )
    multi = models.BooleanField("받는 사람이 줄을 여러 개 추가", default=False)
    allow_outside = models.BooleanField("명부 밖 사람도 링크로 제출", default=False)
    footer_on = models.BooleanField("꼬리말 넣기", default=True)
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    link_token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # 마지막 수정(마감 바꾸기·잠그기·다시 열기 등). 보관 기간(E3 purge)의 활동 기준. update_fields 저장에도 바뀐다.
    updated_at = models.DateTimeField(auto_now=True)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = {*kwargs["update_fields"], "updated_at"}
        super().save(*args, **kwargs)


def get_by_link_or_404(link_token):
    """받는 사람 링크(인증 없음)로 모으기를 찾는다. 공간 소속 확인은 하지 않는 유일한 경로."""
    return get_object_or_404(Collection, link_token=link_token)


class FieldQuerySet(SpaceQuerySet):
    space_lookup = "collection__space"


class Field(models.Model):
    """입력 칸. kind는 만들 때 추정한 형식(사용자가 고칠 수 있음)."""

    class Kind(models.TextChoices):
        TEXT = "text", "글자"
        NUMBER = "number", "숫자"
        SELECT = "select", "선택지"
        DATE = "date", "날짜"
        URL = "url", "링크"
        CALC = "calc", "자동 계산"

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="fields")
    pos = models.PositiveSmallIntegerField("순서")
    name = models.CharField("칸 이름", max_length=100)
    kind = models.CharField("형식", max_length=10, choices=Kind.choices, default=Kind.TEXT)
    options = models.JSONField("선택지", default=list, blank=True)

    objects = FieldQuerySet.as_manager()

    class Meta:
        ordering = ["pos", "id"]

    def __str__(self):
        return self.name


class RowQuerySet(SpaceQuerySet):
    space_lookup = "collection__space"


class Row(models.Model):
    """받는 사람 한 줄. unit = 반·이름 등 줄 머리(unit_head 순서로 보여 줌), who = 낼 사람 이름.

    multi면 같은 unit 줄이 여러 개일 수 있다. member는 명부에서 만든 줄일 때만(나에게 온 것 찾기).
    """

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="rows")
    pos = models.PositiveIntegerField("순서")
    unit = models.CharField("받는 곳", max_length=100)
    who = models.CharField("낼 사람", max_length=100, blank=True)
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="rows")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)

    objects = RowQuerySet.as_manager()

    class Meta:
        ordering = ["pos", "id"]

    def __str__(self):
        return f"{self.unit} {self.who}".strip()


class CellQuerySet(SpaceQuerySet):
    space_lookup = "row__collection__space"


class Cell(models.Model):
    """줄×칸 값 하나. by = 내는 사람(대리 제출이면 실제로 낸 사람, 비워도 됨), at = 마지막으로 낸 시각.

    field는 row와 같은 모으기의 칸이어야 한다(만드는 쪽에서 collection.fields로만 고른다).
    """

    row = models.ForeignKey(Row, on_delete=models.CASCADE, related_name="cells")
    field = models.ForeignKey(Field, on_delete=models.CASCADE, related_name="cells")
    value = models.TextField("값", blank=True)
    by = models.CharField("내는 사람", max_length=100, blank=True)
    at = models.DateTimeField("낸 시각", auto_now=True)

    objects = CellQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["row", "field"], name="uniq_cell_row_field"),
        ]


class Notification(models.Model):
    """🔔 알림 한 건(D3 자동 리마인드). 받는 회원 한 명 것. 같은 줄·종류·날짜에는 하나만(cron이 여러 번 불러도).

    text는 제목·마감 시각만(제출 값은 넣지 않는다). link는 앱 안 경로(/c/<링크>/u/<줄 키>).
    """

    class Kind(models.TextChoices):
        D1 = "d1", "마감 전날"
        D0 = "d0", "마감 당일"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="notifications")
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="notifications")
    row = models.ForeignKey(Row, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField("종류", max_length=10, choices=Kind.choices)
    day = models.DateField("보낸 날")
    text = models.CharField("문구", max_length=300)
    link = models.CharField("링크", max_length=300)
    read = models.BooleanField("읽음", default=False)
    pushed = models.BooleanField("웹 푸시 묶음으로 보냄", default=False)  # G2: 한 번 묶어 보낸 것은 다음 묶음에 다시 넣지 않는다
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["row", "kind", "day"], name="uniq_notice_row_kind_day"),
        ]


class PushSubscription(models.Model):
    """G2 웹 푸시 구독: 회원 한 명의 브라우저(기기) 하나. endpoint는 푸시 서비스 주소(기기마다 다름, 유일).

    푸시 서비스가 404·410을 주면 지운다(collect.push). 같은 브라우저 구독을 다른 회원이 켜면 그 회원 것으로 옮긴다.
    """

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="push_subscriptions")
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.CharField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    # 구독한 PC의 기기 토큰(있으면). 그 PC에서 로그아웃하면 기기 토큰과 함께 지워진다(공용 PC에 앞사람 알림이 뜨지 않게).
    device = models.ForeignKey("accounts.DeviceToken", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="push_subscriptions")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()


class PushRound(models.Model):
    """G2 묶음 한 번(날짜·묶음). cron이 5분마다 불러도 같은 묶음은 한 번만 보내게 하는 표시. 공간 데이터 아님."""

    day = models.DateField()
    slot = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "slot"], name="uniq_push_round")]
