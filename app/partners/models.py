"""N1·N2 협력업체. 업체는 운영자만 등록·공개하고, 교사 추천(Suggestion)은 운영자 확인을 기다릴 뿐 공개되지 않는다.

- 공간에 속하지 않는 공개 자료라 공간 필터 대상이 아니다(양식 자료실과 같음).
- 외부 주소는 slug만 쓴다(읽기 쉬운 한글 slug, 겹치면 -2·-3). 업체를 시드로 넣지 않는다(리드 결정: 가상 업체 금지).
"""
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.validators import URLValidator
from django.db import models

from library.models import make_slug

CATS = ["에듀테크", "졸업앨범·사진", "수학여행·수련활동", "현장체험학습", "전세버스", "찾아가는 교육·체험", "공연·행사", "청소·방역 용역", "인쇄·현수막", "교구·기자재"]  # prototype TR.vendorCats
RESERVED_SLUGS = {"suggest"}  # /partners/suggest
_CAT_CHOICES = [(c, c) for c in CATS]
BADGES = ["서류를 제때 챙겨 줘요", "연락이 빨라요", "안전에 꼼꼼해요", "일정을 잘 지켜요", "가격이 합리적이에요", "아이들이 좋아했어요", "현장 대응이 좋아요", "추가 비용이 없어요"]  # prototype V_BADGES. 번호로 저장하므로 순서를 바꾸지 않는다
REVIEW_TEXT_MAX = 40
AREA_BY_ATPT = {"B10": "서울", "C10": "부산", "D10": "대구", "E10": "인천", "F10": "광주", "G10": "대전", "H10": "울산", "I10": "세종", "J10": "경기",
                "K10": "강원", "M10": "충북", "N10": "충남", "P10": "전북", "Q10": "전남", "R10": "경북", "S10": "경남", "T10": "제주"}  # NEIS 시도교육청 코드
LEVELS = {"초등학교": ("초등", "초"), "중학교": ("중등", "중"), "고등학교": ("고등", "고")}  # 이름 끝 → (표시, 줄임 끝 글자)
MIXED_LEVEL = re.compile(r"초중|중고등|통합")  # 통합학교는 드물어 시도·학교급만으로 학교가 드러난다
REPORT_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 글자(0·O·1·I) 뺌
EDITED_AFTER = timedelta(seconds=1)  # 처음 저장 때 두 시각의 미세한 차이는 고침이 아니다


def new_report_no():
    """공개 신고 번호. 추측 어렵고 '-'로 시작하지 않아 관리 명령에 그대로 넘긴다."""
    return "R-" + "".join(secrets.choice(REPORT_CHARS) for _ in range(8))


def school_labels(school):
    """후기에 저장할 (지역, 학교급). 일반 초·중·고만 시도와 학교급을 보이고, 특수학교·통합학교·판별 실패는 둘 다 뺀다(재식별 방지)."""
    level = next((v[0] for k, v in LEVELS.items() if school.name.endswith(k)), "")
    if not level or MIXED_LEVEL.search(school.name):
        return "", ""
    return AREA_BY_ATPT.get(school.neis_atpt, ""), level


def school_names(school):
    """한 줄 검사에 쓰는 학교 이름: 전체와 줄임(새솔초등학교 → 새솔초)."""
    names = {school.name}
    for ending, (_, short) in LEVELS.items():
        stem = school.name.removesuffix(ending)
        if stem and stem != school.name:
            names.add(stem + short)
    return names


def review_label(area, level):
    return " · ".join(x for x in ["고티처 선생님", area, level] if x)


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


class Review(models.Model):
    """N2 배지 후기. 학교당 업체 1건, 계정당 업체 1건, 쓴 사람만 고치고 지운다. 나쁜 배지·별점은 없다(26번 3-1 ④).

    - 공개에는 badges·확인된 text·area_label·level_label·월 단위 날짜·report_no만 쓴다. member·author_user·school은 응답에 넣지 않는다.
    - 배지는 바로 공개, 한 줄은 운영자 확인(text_state=ok) 뒤 공개. 학생 실명은 필터로 막을 수 없어서다.
    - school은 '학교당 1건'과 명부 검사의 근거. author_user는 '계정당 1건'의 근거이고 후기는 작성자 기준으로 찾는다(자리를 옮겨도 고치게).
    - 쓴 사람이 공간에서 나가면 member가 비어 고칠 수 없다(운영자에게). author_user는 계정이 지워질 때 끊긴다: 나가도 남아야 내린 업체에 다시 못 쓴다.
    - 운영자가 내리면(is_hidden) 공개·집계에서 빠지고, 쓴 사람도 고치거나 지울 수 없다(지우고 다시 쓰면 내린 조치를 우회한다).
    """

    class TextState(models.TextChoices):
        PENDING = "pending", "대기"
        OK = "ok", "공개"
        NO = "no", "반려"

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="reviews")
    member = models.ForeignKey("spaces.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    author_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    school = models.ForeignKey("spaces.School", on_delete=models.PROTECT, related_name="+")
    report_no = models.CharField("신고 번호", max_length=12, unique=True, default=new_report_no, editable=False)
    badges = models.JSONField("배지", help_text="BADGES의 번호 목록")
    text = models.CharField("한 줄", max_length=REVIEW_TEXT_MAX, blank=True)
    text_state = models.CharField("한 줄 상태", max_length=10, choices=TextState.choices, default=TextState.PENDING)
    area_label = models.CharField("지역 표시", max_length=10, blank=True)
    level_label = models.CharField("학교급 표시", max_length=10, blank=True)
    is_hidden = models.BooleanField("운영자가 내림", default=False)
    hidden_reason = models.CharField("내린 까닭", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = verbose_name_plural = "협력업체 후기"
        constraints = [
            models.UniqueConstraint(fields=["partner", "school"], name="uniq_review_partner_school"),
            models.UniqueConstraint(fields=["partner", "author_user"], name="uniq_review_partner_author"),
        ]

    def __str__(self):
        return self.report_no

    @property
    def badge_names(self):
        return [BADGES[i] for i in self.badges]

    @property
    def by_label(self):
        return review_label(self.area_label, self.level_label)

    @property
    def public_text(self):
        return self.text if self.text_state == self.TextState.OK else ""

    @property
    def edited(self):
        return self.updated_at - self.created_at > EDITED_AFTER


class ReviewAction(models.Model):
    """운영자 조치 이력. 후기가 지워져도 번호와 조치가 남는다(한 줄 원문은 남기지 않는다). 까닭·메모에 개인정보를 적지 않는다."""

    class Action(models.TextChoices):
        HIDE = "hide", "내림"
        SHOW = "show", "되돌림"
        DELETE = "delete", "지움"
        TEXT_OK = "text_ok", "한 줄 공개"
        TEXT_NO = "text_no", "한 줄 반려"

    review = models.ForeignKey(Review, on_delete=models.SET_NULL, null=True, blank=True, related_name="actions")
    report_no = models.CharField("신고 번호", max_length=12, db_index=True)
    action = models.CharField("조치", max_length=10, choices=Action.choices)
    reason = models.CharField("까닭", max_length=200, blank=True)
    note = models.CharField("신청인 메모", max_length=200, blank=True)
    by = models.CharField("한 사람", max_length=40, default="운영자")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = verbose_name_plural = "후기 조치 이력"

    def __str__(self):
        return f"{self.report_no} {self.action}"


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
