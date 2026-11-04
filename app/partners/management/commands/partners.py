"""협력업체 운영(운영자, /admin 경로는 열지 않는다):
partners add --name 이름 --cat 분류 --area 지역 --desc 소개 [--phone 번호] [--url 주소] [--tags "가, 나"] [--public]
partners publish <slug> | partners hide <slug>
partners suggestions                       확인 대기 추천 목록
partners suggestion-done <id> [--reject]   추천 처리. 처리한 추천은 다음 purge가 지운다. 업체는 add로 따로 올린다.
"""
import unicodedata

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from partners.models import CATS, Partner, Suggestion


def _plain(value):
    """터미널에 찍는 추천 값: 회원이 쓴 글의 제어 문자(터미널 이스케이프)·줄바꿈을 뺀다."""
    return "".join(ch for ch in str(value) if unicodedata.category(ch) not in {"Cc", "Cf", "Zl", "Zp"})


class Command(BaseCommand):
    help = "협력업체를 등록·공개하고 업체 추천을 처리한다."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        add = sub.add_parser("add")
        add.add_argument("--name", required=True)
        add.add_argument("--cat", required=True, help=" / ".join(CATS))
        add.add_argument("--area", required=True, help="예: 전국, 수도권, 서울, 경기 · 인천, 대전")
        add.add_argument("--desc", required=True)
        add.add_argument("--phone", default="")
        add.add_argument("--url", default="", help="http(s)만")
        add.add_argument("--tags", default="", help="쉼표로 나눈다")
        add.add_argument("--public", action="store_true")
        sub.add_parser("publish").add_argument("slug")
        sub.add_parser("hide").add_argument("slug")
        sub.add_parser("suggestions")
        done = sub.add_parser("suggestion-done")
        done.add_argument("id", type=int)
        done.add_argument("--reject", action="store_true")

    def handle(self, *args, **o):
        action = o["action"]
        if action == "add":
            self.add(o)
        elif action in ("publish", "hide"):
            on = action == "publish"
            if not Partner.objects.filter(slug=o["slug"]).update(is_public=on):
                raise CommandError("그 slug의 업체를 찾지 못했습니다.")
            self.stdout.write(f"{o['slug']}: {'공개' if on else '비공개'}로 바꿨습니다.")
        elif action == "suggestions":
            for s in Suggestion.objects.filter(status=Suggestion.Status.PENDING):
                row = [s.pk, s.created_at.date(), s.name, s.cat, s.contact, s.note, s.by_name, s.school]
                self.stdout.write(" | ".join(_plain(x) for x in row))
        else:
            status = Suggestion.Status.REJECTED if o["reject"] else Suggestion.Status.ACCEPTED
            if not Suggestion.objects.filter(pk=o["id"]).update(status=status):
                raise CommandError("그 번호의 추천을 찾지 못했습니다.")
            self.stdout.write(f"추천 {o['id']}: {status.label}")

    def add(self, o):
        p = Partner(name=o["name"].strip(), cat=o["cat"], area=o["area"].strip(), desc=o["desc"].strip(),
                    phone=o["phone"].strip(), url=o["url"].strip(), tags=o["tags"].strip(), is_public=o["public"])
        try:
            p.full_clean(exclude=["slug"])
        except ValidationError as e:
            raise CommandError(f"값을 확인해 주세요: {', '.join(e.message_dict)}")
        p.save()
        self.stdout.write(f"{p.slug}: {'공개' if p.is_public else '비공개'}로 등록했습니다.")
