from django.core.management.base import BaseCommand

from collect.push import digest


class Command(BaseCommand):
    help = "G2 웹 푸시 묶음: 08:20·12:20·14:40 이후 첫 실행에 안 읽은 🔔를 회원별 한 건으로 보낸다(cron 5분마다, remind 다음)."

    def handle(self, *args, **options):
        n = digest()
        if options["verbosity"]:
            self.stdout.write(f"묶음 알림 {n}명")
