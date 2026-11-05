"""커뮤니티 운영(운영자, /admin 경로는 열지 않는다):
community list-reports   신고된 글·댓글(많은 순). 신고자·쓴 사람은 찍지 않는다. 3건이 넘어도 자동으로 숨기지 않으니 여기서 보고 판단한다.
community hide <key>     글·댓글을 내린다(목록·상세에서 사라지고 주소는 404). 내린 글의 댓글도 함께 안 보인다.
community show <key>     내린 것을 되돌린다.
community delete <key>   글·댓글을 신고 기록과 함께 바로 지운다(되돌릴 수 없다).
community block <닉네임>    차단: 읽기만 되고 글·댓글·신고·닉네임 바꾸기가 막힌다.
community unblock <닉네임>  차단을 푼다.
"""
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max
from django.utils import timezone

from community.models import Comment, CommunityProfile, Post, Report, nickname_key, remove
from partners.management.commands.partners import _plain

KINDS = {Report.Kind.POST: Post, Report.Kind.COMMENT: Comment}
PREVIEW = 200


class Command(BaseCommand):
    help = "커뮤니티 신고를 보고 글·댓글을 내리거나 되돌린다."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("list-reports")
        sub.add_parser("hide").add_argument("key")
        sub.add_parser("show").add_argument("key")
        sub.add_parser("delete").add_argument("key")
        sub.add_parser("block").add_argument("nickname")
        sub.add_parser("unblock").add_argument("nickname")

    def handle(self, *args, **o):
        if o["action"] == "list-reports":
            self.list_reports()
            return
        if o["action"] in ("block", "unblock"):
            self.block(o["nickname"], o["action"] == "block")
            return
        if o["action"] == "delete":
            self.delete(o["key"].strip())
            return
        on = o["action"] == "show"
        when = None if on else timezone.now()
        key = o["key"].strip()
        if not (Post.objects.filter(key=key).update(hidden_at=when) or Comment.objects.filter(key=key).update(hidden_at=when)):
            raise CommandError("그 key의 글·댓글을 찾지 못했습니다.")
        self.stdout.write(f"{key}: {'되돌렸습니다' if on else '내렸습니다'}.")

    def delete(self, key):
        posts, comments = Post.objects.filter(key=key), Comment.objects.filter(key=key)
        if not (posts.exists() or comments.exists()):
            raise CommandError("그 key의 글·댓글을 찾지 못했습니다.")
        remove(posts, comments)
        self.stdout.write(f"{key}: 지웠습니다.")

    def block(self, nickname, on):
        if not CommunityProfile.objects.filter(nickname_key=nickname_key(nickname)).update(blocked_at=timezone.now() if on else None):
            raise CommandError("그 닉네임을 찾지 못했습니다.")
        self.stdout.write(f"{_plain(nickname)}: {'차단했습니다' if on else '차단을 풀었습니다'}.")

    def list_reports(self):
        rows = Report.objects.values("kind", "key").annotate(n=Count("pk"), last=Max("created_at")).order_by("-n", "-last")
        for row in rows:
            target = KINDS[row["kind"]].objects.filter(key=row["key"]).first()
            if target is None:
                continue
            text = f"{target.title} / {target.body}" if row["kind"] == Report.Kind.POST else target.body
            reasons = Counter(Report.objects.filter(kind=row["kind"], key=row["key"]).exclude(reason="").values_list("reason", flat=True))
            shown = ", ".join(f"{Report.Reason(k).label} {v}" for k, v in reasons.items()) or "까닭 없음"
            line = [Report.Kind(row["kind"]).label, row["key"], f"신고 {row['n']}", timezone.localtime(row["last"]).date(), "내림" if target.hidden_at else "공개", shown, text[:PREVIEW]]
            self.stdout.write(" | ".join(_plain(x) for x in line))
