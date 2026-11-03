from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import DEVICE_AGE, GUEST_PREFIX, DeviceToken

GUEST_IDLE = timedelta(days=30)


class Command(BaseCommand):
    help = ("G6 로그인 정리(cron 하루 한 번): 만료 세션, 1년 지난·비활성 사용자의 기기 토큰, "
            "회원 자리 없이 30일 넘게 안 들어온 손님(살아 있는 기기 토큰도 없음). 건수만 출력한다.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="지우지 않고 건수만 센다")

    def handle(self, *args, **options):
        now, dry = timezone.now(), options["dry_run"]
        sessions = Session.objects.filter(expire_date__lt=now)
        tokens = DeviceToken.objects.filter(Q(created_at__lte=now - DEVICE_AGE) | Q(user__is_active=False))
        # 손님 User는 기기 토큰·회원 자리 말고 걸린 것이 없다(낸 값의 내는 사람은 Cell.by 문자열).
        idle = now - GUEST_IDLE
        guests = (
            get_user_model().objects.filter(username__startswith=GUEST_PREFIX, members__isnull=True,
                                            date_joined__lt=idle)
            .filter(Q(last_login__isnull=True) | Q(last_login__lt=idle))
            .exclude(device_tokens__created_at__gt=now - DEVICE_AGE)
        )
        n = {"sessions": sessions.count(), "tokens": tokens.count(), "guests": guests.count()}
        if not dry:
            call_command("clearsessions")
            with transaction.atomic():
                tokens.delete()
                guests.delete()  # 남은 기기 토큰 CASCADE, 세션은 사용자가 없어 무효
        if options["verbosity"]:
            self.stdout.write(f"{'(지우지 않음) ' if dry else ''}만료 세션 {n['sessions']}건 · "
                              f"기기 토큰 {n['tokens']}건 · 주인 없는 손님 {n['guests']}명")
