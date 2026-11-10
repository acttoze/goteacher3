"""School · Space(동학년·학교) · Member. 공간 소속 모델은 SpaceQuerySet.in_space()로만 조회한다."""
import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone


INVITE = "invite"  # 세션 키: 초대 링크로 연 공간의 invite_token(주소에서 빼서 세션에만)


def new_token():
    return secrets.token_urlsafe(24)


class SpaceQuerySet(models.QuerySet):
    """space_lookup: 공간까지 가는 조회 경로. 공간 FK가 없는 자식 모델은 부모를 거쳐 간다(예: "collection__space")."""

    space_lookup = "space"

    def in_space(self, space):
        if space is None or space.pk is None:
            raise ValueError("공간 없이 공간 소속 데이터를 조회할 수 없습니다.")
        return self.filter(**{self.space_lookup: space})


def get_in_space_or_404(model, request, **lookup):
    """request.space(미들웨어가 정함) 안에서만 찾는다. 다른 공간이면 404."""
    return get_object_or_404(model.objects.in_space(request.space), **lookup)


def cls_text(cls):
    """'4-1' → '4학년 1반', '4-전담' → '4학년 전담'. 학년이 앞에 없으면 그대로."""
    head, sep, tail = cls.partition("-")
    if not sep or not head.isdigit():
        return cls
    return f"{head}학년 {tail}반" if tail.isdigit() else f"{head}학년 {tail}"


class School(models.Model):
    class Plan(models.TextChoices):
        FREE = "free", "무료"
        SCHOOL = "school", "학교 플랜"

    neis_atpt = models.CharField("시도교육청 코드", max_length=10)
    neis_code = models.CharField("학교 코드", max_length=10)
    name = models.CharField("학교 이름", max_length=100)
    plan = models.CharField("플랜", max_length=10, choices=Plan.choices, default=Plan.FREE)
    paid_until = models.DateField("결제 기한", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["neis_atpt", "neis_code"], name="uniq_school_neis"),
        ]

    def __str__(self):
        return self.name

    @property
    def writable(self):
        """학교판 기능을 쓸 수 있나. plan=free이거나 결제 기한이 지나면 읽기만(리드 결정: 취합은 계속)."""
        if self.plan != self.Plan.SCHOOL:
            return False
        return self.paid_until is None or self.paid_until >= timezone.localdate()

    @property
    def days_left(self):
        """쓰는 중인 플랜의 남은 날(오늘이 마지막이면 0). 기한이 없거나 끝났으면 None."""
        if not self.writable or self.paid_until is None:
            return None
        return (self.paid_until - timezone.localdate()).days


# U1 소속 색: 흰 바탕에서 점으로 구분되는 중간 채도 10가지. 소속(Member.Group)도 10개라 학교 안에서 겹치지 않는다.
GROUP_PALETTE = {
    "red": ("#e5534b", "빨강"), "orange": ("#e8833a", "주황"), "yellow": ("#d4a017", "노랑"), "green": ("#2f9e5b", "초록"),
    "teal": ("#1a9aa0", "청록"), "blue": ("#3b7dd8", "파랑"), "indigo": ("#5b5bd6", "남색"), "purple": ("#9a4fc9", "보라"),
    "pink": ("#d14d8f", "분홍"), "brown": ("#9b6b43", "갈색"),
}


class Space(models.Model):
    """동학년 공간 또는 학교 공간(kind=school, grade=0, 학교당 하나).

    token: 공간 주소(/s/<token>/). 들어오려면 소속 회원이어야 한다.
    invite_token: 초대 링크(B7). 주소와 따로 두어, 링크가 새면 초대만 새로 발급한다.
    merged_into: 학교 공간으로 합쳐진 옛 동학년(H1). 데이터는 없고 옛 주소만 넘긴다(옛 초대 링크는 폐기, 안내 화면만).
    학교 공간이 지워지면(관리자 없이 마지막 회원이 나감) 옛 공간도 함께 지운다. 학년 유일 제약은 옛 공간도 그대로
    받아, 합쳐진 학교에 같은 학년 동학년을 새로 만들 수 없다.
    """

    class Kind(models.TextChoices):
        GRADE = "grade", "동학년"
        SCHOOL = "school", "학교"

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="spaces")
    kind = models.CharField("종류", max_length=10, choices=Kind.choices, default=Kind.GRADE)
    grade = models.PositiveSmallIntegerField("학년")
    name = models.CharField("이름", max_length=100)
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    invite_token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    merged_into = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="merged_spaces")
    # Q2 학교 공간의 소속 순서(Member.Group 값을 쉼표로). 비면 기본 순서. 빠진 소속은 뒤에 기본 순서로 붙는다.
    group_order = models.CharField("소속 순서", max_length=100, blank=True, default="", db_default="")  # db_default: 옛 상태 이관 테스트가 넣는 행
    # U1 학교 공간의 소속 색(소속=팔레트 키를 쉼표로). 비면 기본(소속 차례대로 GROUP_PALETTE). 관리자가 학교 화면에서 바꾼다.
    group_color = models.CharField("소속 색", max_length=200, blank=True, default="", db_default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 같은 학교·학년 동학년은 하나(사용자 결정 11/3). 동시에 만들어도 DB가 하나만 받는다.
        # 학교 공간은 grade=0이라 같은 제약으로 학교당 하나가 된다(아래 검사가 kind와 grade를 묶는다).
        constraints = [
            models.UniqueConstraint(fields=["school", "grade"], name="uniq_space_school_grade"),
            models.CheckConstraint(
                condition=Q(kind="school", grade=0) | Q(kind="grade", grade__gte=1), name="space_kind_matches_grade"
            ),
        ]

    def __str__(self):
        return f"{self.school} {self.name}"

    @property
    def is_school(self):
        return self.kind == self.Kind.SCHOOL

    @property
    def label(self):
        return "학교" if self.is_school else f"{self.grade}학년"

    @property
    def group_choices(self):
        """[(소속 값, 이름)] 이 학교의 소속 순서(Q2). 학교 화면·취합 소속순이 같이 쓴다."""
        keys = [k for k in self.group_order.split(",") if k in Member.Group.values]
        keys = list(dict.fromkeys(keys + Member.Group.values))
        return [(k, Member.Group(k).label) for k in keys]

    @property
    def group_colors(self):
        """{소속: 팔레트 키}. 저장한 값이 기본(소속 차례대로)을 덮는다. 바꿀 때 서로 맞바꾸므로(spaces.school.group_color) 겹치지 않는다."""
        out = dict(zip(Member.Group.values, GROUP_PALETTE))
        for pair in self.group_color.split(","):
            g, _, k = pair.partition("=")
            if g in out and k in GROUP_PALETTE:
                out[g] = k
        return out

    @property
    def group_hex(self):
        """{소속: 색 값}. 학교 공간만(동학년은 소속이 없어 점을 찍지 않는다)."""
        return {g: GROUP_PALETTE[k][0] for g, k in self.group_colors.items()} if self.is_school else {}

    @property
    def word(self):
        """화면 문구의 공간 낱말: 동학년 / 학교. 템플릿에서는 space_word(spaces.context)."""
        return self.get_kind_display()


class Member(models.Model):
    """이름만 고르고 들어온 사람도 Member.

    user: 구글 회원(google:<sub>) 또는 초대 링크로 이름만 들어온 손님(guest:<토큰>, accounts.is_guest).
    손님도 User라서 기기 토큰·소속 확인 미들웨어를 그대로 쓴다. 구글을 이으면 absorb_guest로 합친다.
    group: 학교 공간의 소속(prototype TR.groups). 동학년 공간에서는 비어 있고, 합칠 때 학년으로 채운다.
    is_manager: 학교 관리자. 구글 회원만 된다(손님 자리는 반·이름으로 찾기 쉬워서). 직위 글자로 권한을 주지 않는다.
    can_duty: 복무 입력자(J1, 교무실무사). 관리자가 지정하고 구글 회원만 된다.
    can_repair: 수리 담당자(K1). 관리자가 지정하고 구글 회원만 된다. 행정실 그룹이어도 이 값이 없으면 처리 권한이 없다.
    can_meal: 급식 담당자(M1). 관리자가 지정하고 구글 회원만 된다. 급식소 그룹이어도 이 값이 없으면 권한이 없다.
    """

    class Group(models.TextChoices):
        G1 = "g1", "1학년"
        G2 = "g2", "2학년"
        G3 = "g3", "3학년"
        G4 = "g4", "4학년"
        G5 = "g5", "5학년"
        G6 = "g6", "6학년"
        OFFICE = "office", "교무실"
        ADMIN = "admin", "행정실"
        MEAL = "meal", "급식소"
        SUPPORT = "support", "기타/지원"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="members"
    )
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    name = models.CharField("이름", max_length=40)
    cls = models.CharField("반", max_length=20, help_text="예: 4-1, 4-전담")
    position = models.CharField("직위", max_length=40, blank=True)
    group = models.CharField("소속", max_length=10, choices=Group.choices, blank=True, default="")
    is_manager = models.BooleanField("학교 관리자", default=False)
    can_duty = models.BooleanField("복무 입력자", default=False)
    can_repair = models.BooleanField("수리 담당자", default=False)
    can_meal = models.BooleanField("급식 담당자", default=False)
    # G2 설정 > 알림: 끈 웹 푸시 묶음 키(collect.push.SLOTS의 am·noon·pm, 쉼표로). 비면 다 켜짐. 🔔은 그대로 쌓인다.
    push_off = models.CharField("끈 알림 묶음", max_length=20, blank=True, default="")
    # Q1 설정 > 표시 요일: 일정 주·월 보기에 보일 요일(월=0~일=6 숫자를 이어서). 기본 월~금.
    days_on = models.CharField("표시 요일", max_length=7, default="01234")
    # Q2 소속 안 순서(관리자가 끌어서 정함). 비면 직책 순(school._order).
    order = models.PositiveIntegerField("소속 안 순서", null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["space", "user"], name="uniq_member_space_user"),
        ]

    def __str__(self):
        return f"{self.name}({self.cls})"

    @property
    def shown_days(self):
        """켠 요일(월=0~일=6) 집합. 값이 비거나 틀리면 월~금."""
        return {int(c) for c in self.days_on if c in "0123456"} or set(range(5))

    @property
    def in_grade(self):
        """학년 반·전담인가(교무실·행정실 등이 아니라)."""
        return bool(cls_group(self.cls))

    @property
    def cls_text(self):
        """화면 표시: '4학년 1반', 학년 밖 교직원은 '교무실 · 교무부장'."""
        if self.position and not self.in_grade:
            return f"{self.cls} · {self.position}"
        return cls_text(self.cls)


def cls_group(cls):
    """'4-1' → 'g4'. 학년 반이 아니면 빈 값."""
    group = "g" + cls.partition("-")[0]
    return group if group in Member.Group.values else ""


def lead_member(space):
    """공간 대표 한 명: 동학년은 가장 먼저 들어온 선생님, 학교 공간은 관리자 중 가장 먼저 들어온 분(없으면 None).
    새 기기 알림을 받고 화면에 대표로 보인다. 권한 판단은 leads()로 한다."""
    members = Member.objects.in_space(space)
    if space.is_school:
        members = members.filter(is_manager=True)
    return members.order_by("joined_at", "pk").first()


def leads(space, member):
    """대표 권한(초대 재발급·내보내기·주인 없는 취합): 동학년은 대표 한 명, 학교 공간은 관리자 전원."""
    if space.is_school:
        return member.is_manager
    lead = lead_member(space)
    return lead is not None and lead.pk == member.pk


class Duty(models.Model):
    """업무분장 한 줄(H3). 담당은 이름 글자로 저장하고 화면에서 명부와 짝짓는다(전출입·미입장 선생님도 표 그대로 남게)."""

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="duties")
    dept = models.CharField("부서", max_length=100)
    task = models.CharField("업무", max_length=200)
    who = models.CharField("담당", max_length=100, blank=True)
    order = models.PositiveIntegerField("순서")

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return f"{self.dept} {self.task}"


class MemberLog(models.Model):
    """학교 명부 이력(H3): 그룹·직책 고침과 관리자 지정·해제. 나간 뒤에도 읽히게 이름 글자로 저장한다."""

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="member_logs")
    by = models.CharField("한 사람", max_length=40)
    name = models.CharField("대상", max_length=40)
    before = models.CharField("전", max_length=100)
    after = models.CharField("후", max_length=100)
    at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return f"{self.name}: {self.before} → {self.after}"


class Attendance(models.Model):
    """오늘 복무 한 줄(J1). 유형·시간만, 사유 칸은 두지 않는다. 이름은 글자로 저장한다.
    member: 명부에서 이름이 하나로 맞을 때만(소속 표시용). 권한에 쓰지 않는다. 동명이인·명부에 없으면 비운다.
    by: 넣은 사람. 일정 화면에서 한 줄을 지울 수 있는 사람은 by·관리자·복무 입력자."""

    KINDS = ["출장", "외출", "조퇴", "연가", "휴가", "지각", "부재"]  # 부재: 육아시간 등 가족·건강이 드러나는 유형

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="attendance")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)  # 일정 화면의 지우기 주소(순번 ID 대신)
    date = models.DateField("날짜")
    name = models.CharField("이름", max_length=40)
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    kind = models.CharField("유형", max_length=4, choices=[(k, k) for k in KINDS])
    time_text = models.CharField("시간", max_length=20, blank=True)
    by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SpaceQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["space", "date"])]

    def __str__(self):
        return self.title

    @property
    def title(self):
        return f"{self.name} {self.kind}"

    @property
    def group_label(self):
        return self.member.get_group_display() if self.member else ""


class PlanRequest(models.Model):
    """학교 플랜 신청(O1). 공간이 아직 없을 수 있어 공간 대신 신청자(user)에게 속한다: 조회는 언제나 user로 좁힌다.
    key: 견적서 주소. user: 구글 회원(첫 관리자로 세울 사람). member: 신청 때 이 학교 회원이었으면 그 자리.
    학생·학급 수는 숫자만. 처리한 날(대기 중이면 접수한 날)부터 1년 뒤 지운다(hwpfiles.retention)."""

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        DONE = "done", "처리함"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="plan_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    by_name = models.CharField("담당자 이름", max_length=40)
    position = models.CharField("직위", max_length=40)
    email = models.EmailField("연락 메일")
    students = models.PositiveIntegerField("학생 수", null=True, blank=True)
    classes = models.PositiveIntegerField("학급 수", null=True, blank=True)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField("처리한 때", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["school", "user"], condition=Q(status="pending"), name="uniq_plan_request_pending"),
        ]

    def __str__(self):
        return f"신청 {self.pk}"


def is_last_manager(member):
    """이 자리가 빠지면 공간에 관리자가 없어지나. 동시 요청은 부르는 쪽이 공간 행을 잠가 직렬화한다."""
    return member.is_manager and not (
        Member.objects.filter(space_id=member.space_id, is_manager=True).exclude(pk=member.pk).exists()
    )


def absorb_guest(guest, user):
    """손님(guest)의 회원 자리를 user로 옮기고 손님을 지운다. 같은 공간에 둘 다 있으면 user 쪽을 남긴다.

    손님의 기기 토큰·세션은 옮기지 않는다(같은 반·이름으로 들어온 다른 PC가 구글 계정을 얻지 않게).
    손님 User를 지우면 그 기기 토큰(CASCADE)과 세션(사용자 없음)이 함께 끊긴다.
    """
    Member.objects.filter(user=guest).exclude(space__members__user=user).update(user=user)
    Member.objects.filter(user=guest).delete()
    guest.delete()
