from django.core.management.base import BaseCommand

from collect.bell import remind


class Command(BaseCommand):
    help = "D3 자동 리마인드: 마감 전날·당일 08:20 이후 아직 안 낸 회원의 🔔에 한 번씩 쌓는다(cron 5분마다)."

    def handle(self, *args, **options):
        n = remind()
        if options["verbosity"]:
            self.stdout.write(f"리마인드 {n}건")
