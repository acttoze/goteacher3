from django.core.management.base import BaseCommand
from django.utils import timezone

from hwpfiles.retention import purge


class Command(BaseCommand):
    help = ("E3 보관 기간 자동 삭제(cron 하루 한 번): 마감 후 1년 지난 모으기(제출 값·올린 파일·결과 파일), "
            "모으기에 안 쓴 올린 파일(24시간), 멈춘 한글 작업, 고아 파일, 저장 크기 정합. 건수만 출력한다.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="지우지 않고 건수만 센다")

    def handle(self, *args, **options):
        n = purge(timezone.now(), dry_run=options["dry_run"])
        if options["verbosity"]:
            self.stdout.write(
                f"{'(지우지 않음) ' if options['dry_run'] else ''}"
                f"지난 모으기 {n['collections']}건 · 올린 파일 {n['sources']}건 · 멈춘 작업 {n['stuck']}건 · "
                f"고아 파일 {n['orphans']}개(못 지움 {n['failed']}개) · 저장 크기 고침 {n['stored']}건"
            )
