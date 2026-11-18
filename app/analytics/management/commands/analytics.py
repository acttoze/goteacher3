"""V10 고객 분석 요약(리드): analytics --since 7d   (단위 d, 기본 7d). 화면 /analytics의 첫 줄·깔때기·학교별을 평문으로."""
import re

from django.core.management.base import BaseCommand, CommandError

from analytics.stats import build

SINCE = re.compile(r"([1-9]\d{0,3})d")


class Command(BaseCommand):
    help = "새 고티처 이용·의견 요약을 평문으로 찍는다."

    def add_arguments(self, parser):
        parser.add_argument("--since", default="7d", help="최근 며칠(오늘 포함, 예: 7d)")

    def handle(self, *args, **o):
        m = SINCE.fullmatch(o["since"].strip())
        if not m:
            raise CommandError("--since는 7d처럼 숫자와 d(일)로 써 주세요.")
        s = build(int(m.group(1)))
        self.stdout.write(f"{s['start']} ~ {s['today']}")
        for label, n, note in s["tiles"]:
            self.stdout.write(f"{label}: {n} ({note})")
        self.stdout.write("깔때기: " + " → ".join(f"{label} {n}({pct}%)" for label, n, pct in s["funnel"]))
        self.stdout.write(" | ".join(("학교", "대상", "들어와 봄", "비율", "기간 이용", "의견", "돌아감", "마지막 이용")))
        for r in s["schools"]:
            self.stdout.write(" | ".join(str(x) for x in (r["name"], r["target"], r["entered"], f"{r['pct']}%", r["active"],
                                                          r["feedback"], r["back"], r["last"] or "-")))
