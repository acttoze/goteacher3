from django.core.management.base import BaseCommand
from django.utils import timezone

from hwpfiles.retention import purge


class Command(BaseCommand):
    help = ("E3 보관 기간 자동 삭제(cron 하루 한 번): 마감 후 1년 지난 모으기(제출 값·올린 파일·결과 파일), "
            "모으기에 안 쓴 올린 파일(24시간), 지난 복무(7일), 지난 수리 요청(완료 90일·그 밖 1년), 업체 접속 기록·지난 업체 계정(30일), 권한 변경 기록(1년), 처리했거나 1년 지난 업체 추천, 플랜이 끝나고 1년 지난 공지, 신청 정보(처리 뒤 1년), 나간 회원의 나만 일정, 멈춘 한글 작업, 고아 파일, 저장 크기 정합. 건수만 출력한다.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="지우지 않고 건수만 센다")

    def handle(self, *args, **options):
        n = purge(timezone.now(), dry_run=options["dry_run"])
        if options["verbosity"]:
            self.stdout.write(
                f"{'(지우지 않음) ' if options['dry_run'] else ''}"
                f"지난 모으기 {n['collections']}건 · 올린 파일 {n['sources']}건 · 지난 복무 {n['attendance']}건 · 지난 수리 요청 {n['repairs']}건 · 지난 식판 사진 {n['meal_photos']}건 · 업체 접속 기록 {n['vendor_access']}건 · 지난 업체 계정 {n['vendor_accounts']}건 · 지난 권한 기록 {n['member_logs']}건 · 업체 추천 {n['suggestions']}건 · 지난 후기 {n['reviews']}건 · 지난 공지 {n['notices']}건 · 신청 정보 {n['plan_requests']}건 · 나간 회원의 나만 일정 {n['my_events']}건 · 멈춘 작업 {n['stuck']}건 · "
                f"고아 파일 {n['orphans']}개(못 지움 {n['failed']}개) · 저장 크기 고침 {n['stored']}건"
            )
