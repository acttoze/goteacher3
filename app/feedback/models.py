"""V6 의견 보내기: 계정 회원이 공간 화면에서 보낸 한 줄 의견과 '예전 고티처로' 돌아가는 이유.

화면 내용은 받지 않는다. 경로는 주소 규칙 모양만(views.path_shape), 화면 크기는 '1536x750' 모양만.
"""
from django.db import models

from spaces.models import Member, Space, SpaceQuerySet

TEXT_MAX = 1000


class Feedback(models.Model):
    class Kind(models.TextChoices):
        NONE = "", "고르지 않음"
        BAD = "bad", "불편"
        BUG = "bug", "오류"
        WISH = "wish", "바라는 점"
        GOOD = "good", "좋아요"
        BACK_MISSING = "back_missing", "돌아감 · 찾는 게 없어요"
        BACK_UNFAMILIAR = "back_unfamiliar", "돌아감 · 익숙지 않아요"
        BACK_SLOW = "back_slow", "돌아감 · 느려요"
        BACK_DATA = "back_data", "돌아감 · 데이터가 달라요"
        BACK_BROWSE = "back_browse", "돌아감 · 그냥 둘러봤어요"
        BACK_SKIP = "back_skip", "돌아감 · 건너뜀"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="feedback")
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")  # 나가면 자리만 끊기고 의견은 남는다
    kind = models.CharField("종류", max_length=20, choices=Kind.choices, blank=True, default="")
    text = models.TextField("글", max_length=TEXT_MAX, blank=True, default="")
    path = models.CharField("화면 경로 모양", max_length=200, blank=True, default="")
    screen = models.CharField("화면 크기", max_length=11, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = SpaceQuerySet.as_manager()

    @property
    def is_back(self):
        return self.kind.startswith("back_")

    @property
    def group_text(self):
        """이름 대신 소속(학교 소속 이름, 없으면 반 표시). 나간 자리는 '나간 선생님'."""
        m = self.member
        if m is None:
            return "나간 선생님"
        return self.space.group_label(m.group) or m.cls_text
