"""Q10 커뮤니티: 전국 계정 회원이 닉네임으로 이야기하는 곳.

- 어느 공간에도 속하지 않는다(전국 하나). 그래서 공간 필터(`in_space`) 대상이 아니고, 대신 views의 게이트가 공간에 속한 계정 회원만 들인다.
- author는 서버 안에만 있다. 화면 표시는 닉네임(CommunityProfile)뿐이고 없으면 '나간 선생님'. 쓸 때의 닉네임을 글에 따로 저장하지 않는다(바꾸면 옛 글도 같이 바뀐다).
- 계정을 지우면 프로필은 CASCADE, 글·댓글은 남고 author만 비어 '나간 선생님'으로 나온다. 신고는 신고자 연결만 끊긴다.
- 운영자가 내린 글·댓글은 쓴 사람이 지울 수 있고, 내린 뒤 90일이 지나면 지운다(hwpfiles.retention). 실제 삭제는 remove 하나로.
"""
import re
import secrets
import unicodedata

from django.conf import settings
from django.db import models, transaction

GONE_NAME = "나간 선생님"
NICK_MIN = 2
NICK_MAX = 12
TITLE_MAX = 100
BODY_MAX = 5000
COMMENT_MAX = 500
NICK_RE = re.compile(r"[가-힣A-Za-z0-9]+(?: [가-힣A-Za-z0-9]+)?")  # 허용 목록: 완성형 한글·영문·숫자, 가운데 공백 한 칸까지. 자모·한자·보이지 않는 문자는 여기서 걸린다


def new_key():
    """16진수만: 운영자가 관리 명령에 그대로 붙여도 '-'로 시작해 옵션으로 읽히지 않는다."""
    return secrets.token_hex(24)


def clean_nickname(raw):
    """검사하고 저장할 모양: 호환 문자를 펴고(NFKC) 앞뒤 공백만 뗀다. 그 밖의 문자는 고치지 않고 NICK_RE가 거부한다."""
    return unicodedata.normalize("NFKC", str(raw or "")).strip()


def nickname_key(nickname):
    """겹침을 볼 값: 대소문자·공백을 무시한다('Kim Tea' = 'kimtea')."""
    return "".join(unicodedata.normalize("NFKC", nickname).casefold().split())


def shown_name(author):
    profile = getattr(author, "community_profile", None) if author else None
    return profile.nickname if profile else GONE_NAME


class CommunityProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_profile")
    nickname = models.CharField("닉네임", max_length=NICK_MAX)
    nickname_key = models.CharField("겹침 확인 값", max_length=48, unique=True)  # 같은 닉네임은 같은 값이라 nickname 유일도 함께 보장된다
    nickname_changed_at = models.DateTimeField("닉네임 정한 때")
    blocked_at = models.DateTimeField("운영자가 차단한 때", null=True, blank=True)  # 읽기만 된다. 닉네임도 못 바꾼다

    def __str__(self):
        return self.nickname


class Post(models.Model):
    key = models.CharField(max_length=64, unique=True, default=new_key, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    title = models.CharField("제목", max_length=TITLE_MAX)
    body = models.TextField("본문")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    hidden_at = models.DateTimeField("운영자가 내린 때", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.title

    @property
    def by(self):
        return shown_name(self.author)


class Comment(models.Model):
    key = models.CharField(max_length=64, unique=True, default=new_key, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    body = models.TextField("댓글")
    created_at = models.DateTimeField(auto_now_add=True)
    hidden_at = models.DateTimeField("운영자가 내린 때", null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return self.key

    @property
    def by(self):
        return shown_name(self.author)


class Report(models.Model):
    """신고. 쌓여도 자동으로 숨기지 않고 운영자가 관리 명령(community)으로 판단한다."""

    class Kind(models.TextChoices):
        POST = "post", "글"
        COMMENT = "comment", "댓글"

    class Reason(models.TextChoices):
        STUDENT = "student", "학생 개인정보"
        PERSON = "person", "특정인을 알아볼 수 있는 내용"
        ABUSE = "abuse", "비방·욕설"
        OTHER = "other", "그 밖의 문제"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    key = models.CharField("대상 key", max_length=64)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reason = models.CharField("까닭", max_length=10, choices=Reason.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "key", "reporter"], name="one_report_per_target")]
        indexes = [models.Index(fields=["kind", "key"], name="report_target")]


def remove(posts, comments):
    """글·댓글을 신고 기록과 함께 실제로 지운다(본인 지우기·운영자 delete·보관 기간). 글의 댓글은 CASCADE."""
    with transaction.atomic():
        Report.objects.filter(kind=Report.Kind.COMMENT, key__in=Comment.objects.filter(post__in=posts).values("key")).delete()
        Report.objects.filter(kind=Report.Kind.COMMENT, key__in=comments.values("key")).delete()
        Report.objects.filter(kind=Report.Kind.POST, key__in=posts.values("key")).delete()
        comments.delete()
        posts.delete()
