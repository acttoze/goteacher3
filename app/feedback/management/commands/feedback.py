"""V6 의견 읽기(리드): feedback --since 7d   (단위 d·h, 기본 7d)

최근순 평문 표: 보낸 때 | 종류 | 공간 | 소속 | 경로 모양 | 화면 | 글. 이름은 찍지 않는다. 글의 줄바꿈·제어 문자는 한 줄로 편다.
"""
import re
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from feedback.models import Feedback
from partners.management.commands.partners import _plain

SINCE = re.compile(r"(\d{1,4})([dh])")


class Command(BaseCommand):
    help = "최근 받은 의견을 평문 표로 찍는다."

    def add_arguments(self, parser):
        parser.add_argument("--since", default="7d", help="얼마 전부터(예: 7d, 24h)")

    def handle(self, *args, **o):
        m = SINCE.fullmatch(o["since"].strip())
        if not m:
            raise CommandError("--since는 7d·24h처럼 숫자와 d(일)·h(시간)로 써 주세요.")
        n, unit = int(m.group(1)), m.group(2)
        start = timezone.now() - (timedelta(days=n) if unit == "d" else timedelta(hours=n))
        rows = Feedback.objects.filter(created_at__gte=start).select_related("space", "member").order_by("-created_at", "-pk")
        self.stdout.write(" | ".join(("보낸 때", "종류", "공간", "소속", "경로", "화면", "글")))
        count = 0
        for f in rows:
            count += 1
            line = (timezone.localtime(f.created_at).strftime("%m-%d %H:%M"), f.get_kind_display(), f.space.name, f.group_text,
                    f.path or "-", f.screen or "-", " ".join(f.text.split()) or "-")
            self.stdout.write(" | ".join(_plain(x) for x in line))
        self.stdout.write(f"모두 {count}건")
