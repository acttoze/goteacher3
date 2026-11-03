"""K1 수리 요청(prototype repairTab·TR.seedRepairs). 교내 한 곳, 시설·정보. 조회는 SpaceQuerySet.in_space()로만.

- by: 고치기·지우기 권한의 근거(나가면 비고, 관리자만 지운다). by_name: 요청 때의 표시 값.
- photo: 서버가 재인코딩한 JPEG만(repairs.photo). 경로에 올린 이름을 쓰지 않고, 내려받기는 회원 확인 뷰로만.
  저장량(stored)은 한글 파일·공지 첨부와 같은 한도(SourceFile.used_bytes), 행이 지워지면 파일도 지운다(hwpfiles.retention).
- 보관: 완료 90일 뒤, 끝나지 않은 요청도 마지막 변경 1년 뒤 행·사진을 지운다(hwpfiles.retention, 사진에 교실 모습이 찍힌다).
"""
import secrets

from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import hash_token
from spaces.models import Space, SpaceQuerySet, new_token


def _photo_path(instance, filename):
    """올린 파일 이름은 경로에 쓰지 않는다. 사진을 바꾸면 새 이름(옛 파일은 커밋 뒤 지운다)."""
    return f"repair/{instance.space_id}/{new_token()}.jpg"


class Repair(models.Model):
    class Cat(models.TextChoices):
        FACILITY = "facility", "시설"
        INFO = "info", "정보"

    class Status(models.TextChoices):
        RECEIVED = "received", "접수"
        VISIT = "visit", "방문 예정"
        DOING = "doing", "처리 중"
        DONE = "done", "완료"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="repairs")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    cat = models.CharField("분류", max_length=10, choices=Cat.choices)
    place = models.CharField("위치", max_length=100, blank=True)
    title = models.CharField("무엇이 안 되나요", max_length=200)
    note = models.CharField("메모", max_length=1000, blank=True)
    urgent = models.BooleanField("급함", default=False)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.RECEIVED)
    reply = models.CharField("답변", max_length=500, blank=True)
    by = models.ForeignKey("spaces.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    by_name = models.CharField("요청한 사람", max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    photo = models.FileField(upload_to=_photo_path, blank=True)
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(바이트)", default=0)
    reply_vendor = models.BooleanField("업체가 쓴 답변", default=False)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return self.key

    @property
    def is_done(self):
        return self.status == self.Status.DONE

    @property
    def status_pill(self):
        return {self.Status.RECEIVED: "neg", self.Status.DONE: "pos"}.get(self.status, "acc")


class VendorAccount(models.Model):
    """K2 업체 계정: 학교마다 살아 있는 계정 1개(재발급은 옛 행을 해제하고 새 행). User·Member·세션을 만들지 않는다.

    - 원문 링크 토큰은 저장하지 않는다. 기기 쿠키는 토큰에서 한 방향으로 만들어(cookie_value) 한 링크로 여러 PC가 들어와도
      같은 값이고, 재발급하면 함께 끊긴다. DB가 새어도 token_hash·cookie_hash로는 링크도 쿠키도 만들 수 없다.
    - visible_from: 업체에 보이는 요청의 시작. 첫 발급·해제 뒤 새 발급은 그 시각, 재발급(담당자 교체)은 이어받는다.
      요청의 생성 시각으로 본다(옛 요청을 나중에 정보로 바꿔도 안 보인다).
    - 담당자 이름이 들어 있어 해제·기간 만료 30일 뒤 행을 지운다(hwpfiles.retention).
    """

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="vendor_accounts")
    name = models.CharField("업체 이름", max_length=100)
    contact_name = models.CharField("담당자 이름", max_length=40)
    until = models.DateField("사용 기간 끝")
    visible_from = models.DateTimeField("이 시각 뒤 요청만 보임", default=timezone.now)
    token_hash = models.CharField(max_length=64, unique=True)
    cookie_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey("spaces.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["space"], condition=Q(revoked_at__isnull=True), name="uniq_live_vendor_per_space")]

    def __str__(self):
        return self.name

    @staticmethod
    def cookie_value(token):
        return hash_token(f"vendor-cookie:{token}")

    @classmethod
    def issue(cls, space, name, contact_name, until, by, visible_from=None):
        """(계정, 링크 토큰 원문). 원문은 여기서 한 번만 나온다."""
        token = secrets.token_urlsafe(32)
        return cls.objects.create(space=space, name=name, contact_name=contact_name, until=until, invited_by=by,
                                  visible_from=visible_from or timezone.now(),
                                  token_hash=hash_token(token), cookie_hash=hash_token(cls.cookie_value(token))), token

    @classmethod
    def current(cls, space):
        """해제하지 않은 계정(기간이 지났어도 담당자가 다시 발급하도록 보여 준다) 또는 None."""
        return cls.objects.in_space(space).filter(revoked_at__isnull=True).select_related("invited_by", "space__school").first()

    def repairs(self):
        """업체가 보거나 바꿀 수 있는 요청 전부. 목록·사진·상태 변경이 모두 여기서 시작한다."""
        return Repair.objects.in_space(self.space).filter(cat=Repair.Cat.INFO, created_at__gte=self.visible_from)

    @property
    def issuer_gone(self):
        """링크를 받아 본 선생님이 더는 담당이 아니다(나갔거나 담당에서 빠짐): 재발급을 권한다."""
        m = self.invited_by
        return m is None or not (m.can_repair or m.is_manager)

    @property
    def in_period(self):
        return self.revoked_at is None and self.until >= timezone.localdate()

    @property
    def is_open(self):
        """업체가 지금 들어올 수 있나: 해제 안 됨 + 사용 기간 안 + 학교 플랜 살아 있음."""
        return self.in_period and self.space.is_school and self.space.school.writable


class VendorAccessQuerySet(SpaceQuerySet):
    space_lookup = "account__space"


class VendorAccess(models.Model):
    """접속 기록(30일). IP는 저장하지 않는다. repair_key: 상태 변경·사진 열람의 요청(요청이 지워져도 기록은 남게 FK가 아니다)."""

    class Action(models.TextChoices):
        ENTER = "enter", "링크로 들어옴"
        VIEW = "view", "화면 열어 봄"
        STATE = "state", "상태·답변 바꿈"
        PHOTO = "photo", "사진 열어 봄"

    account = models.ForeignKey(VendorAccount, on_delete=models.CASCADE, related_name="accesses")
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    repair_key = models.CharField(max_length=64, blank=True)

    objects = VendorAccessQuerySet.as_manager()
