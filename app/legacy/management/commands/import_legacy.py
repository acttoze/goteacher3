"""P3 구버전 이관: manage.py import_legacy <덤프 폴더> [--school 코드 …] [--dry-run] [--report 경로.csv]

덤프 폴더는 ops/legacy_extract.sh가 만든 것(표별 JSON Lines + files/). 다시 돌리면 새것만 더한다.
--school이 없으면 D1 대상 학교 전부. --dry-run은 첨부를 복사하지 않고 모두 되돌린다(판별은 한다).
출력과 보고서에는 이름·아이디·내용을 쓰지 않는다.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from legacy.importer import run


class Command(BaseCommand):
    help = "구버전 고티처 덤프를 학교 공간으로 옮긴다(P3)."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--school", nargs="+", help="학교 코드(여럿이면 빈칸으로). 없으면 D1 대상 전부")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--report", help="보고서 CSV 경로(버린 항목 목록은 같은 곳에 _items.csv로)")

    def handle(self, *args, **o):
        try:
            started = time.monotonic()
            report, codes, items = run(o["folder"], o["school"], o["dry_run"], o["report"])
        except FileNotFoundError as e:
            raise CommandError(f"덤프 파일을 찾지 못했습니다: {e.filename}")
        took = time.monotonic() - started
        self.stdout.write(f"{'[마른 실행, 모두 되돌림] ' if o['dry_run'] else ''}학교 {len(codes)}곳 · {took:.0f}초 · 첨부 저장 위치 {settings.MEDIA_ROOT}")
        for line in report.summary():
            self.stdout.write(line)
        if o["report"]:
            self.stdout.write(f"보고서: {o['report']} · {items}")
