"""협력업체 운영(운영자, /admin 경로는 열지 않는다):
partners add --name 이름 --cat 분류 --area 지역 --desc 소개 [--phone 번호] [--url 주소] [--tags "가, 나"] [--public]
partners publish <slug> | partners hide <slug>
partners suggestions                       확인 대기 추천 목록
partners suggestion-done <id> [--reject]   추천 처리. 처리한 추천은 다음 purge가 지운다. 업체는 add로 따로 올린다.
partners reviews --partner <slug> | --pending   후기 목록(작성자·학교는 찍지 않는다). --pending은 확인 기다리는 한 줄
partners review-text-ok <번호> | review-text-no <번호> [--reason 까닭]   한 줄 공개 / 반려
partners review-hide <번호> --reason 까닭 [--note 신청인 메모] [--by 이름]   공개·집계에서 뺀다
partners review-show <번호> [--reason 까닭]   내린 후기를 되돌린다(내린 까닭은 남는다)
partners review-delete <번호> --reason 까닭   지운다(학교·계정 자리도 풀린다). 번호와 조치는 이력에 남는다
<번호>는 화면에 보이는 신고 번호(R-…). 까닭·메모에 개인정보를 적지 않는다.
"""
import unicodedata

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from partners.models import CATS, Partner, Review, ReviewAction, Suggestion


def _plain(value):
    """터미널에 찍는 추천 값: 회원이 쓴 글의 제어 문자(터미널 이스케이프)·줄바꿈을 뺀다."""
    return "".join(ch for ch in str(value) if unicodedata.category(ch) not in {"Cc", "Cf", "Zl", "Zp"})


NO_PII = "개인정보(이름·연락처·학교)를 적지 마세요."
ACTIONS_NEED_REASON = {ReviewAction.Action.HIDE: True, ReviewAction.Action.DELETE: True}
ACTIONS = {"review-hide": ReviewAction.Action.HIDE, "review-show": ReviewAction.Action.SHOW, "review-delete": ReviewAction.Action.DELETE,
           "review-text-ok": ReviewAction.Action.TEXT_OK, "review-text-no": ReviewAction.Action.TEXT_NO}


class Command(BaseCommand):
    help = "협력업체를 등록·공개하고 업체 추천과 후기를 처리한다."

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
        which = sub.add_parser("reviews").add_mutually_exclusive_group(required=True)
        which.add_argument("--partner")
        which.add_argument("--pending", action="store_true")
        for name, must in (("review-hide", True), ("review-show", False), ("review-delete", True), ("review-text-ok", False), ("review-text-no", False)):
            act = sub.add_parser(name)
            act.add_argument("number", help="신고 번호(R-…)")
            act.add_argument("--reason", required=must, default="", help=NO_PII)
            act.add_argument("--note", default="", help="신청인 메모. " + NO_PII)
            act.add_argument("--by", default="운영자")

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
        elif action == "reviews":
            self.reviews(o)
        elif action in ACTIONS:
            self.review_action(ACTIONS[action], o)
        else:
            status = Suggestion.Status.REJECTED if o["reject"] else Suggestion.Status.ACCEPTED
            if not Suggestion.objects.filter(pk=o["id"]).update(status=status):
                raise CommandError("그 번호의 추천을 찾지 못했습니다.")
            self.stdout.write(f"추천 {o['id']}: {status.label}")

    def reviews(self, o):
        if o["pending"]:
            rows = Review.objects.filter(text_state=Review.TextState.PENDING).exclude(text="").order_by("created_at", "pk")
        else:
            partner = Partner.objects.filter(slug=o["partner"]).first()
            if partner is None:
                raise CommandError("그 slug의 업체를 찾지 못했습니다.")
            rows = partner.reviews.all()
        for r in rows.select_related("partner"):
            row = [r.report_no, r.partner.slug, r.created_at.date(), r.by_label, "내림" if r.is_hidden else "공개",
                   ", ".join(r.badge_names), r.get_text_state_display(), r.text, r.hidden_reason]
            self.stdout.write(" | ".join(_plain(x) for x in row))

    def review_action(self, action, o):
        reason, note = o["reason"].strip(), o["note"].strip()
        if ACTIONS_NEED_REASON.get(action) and not reason:
            raise CommandError("까닭을 써 주세요.")
        for value in (reason, note):
            if len(value) > 200:
                raise CommandError("까닭과 메모는 200자 안으로 써 주세요.")
        number = o["number"].strip().upper()
        with transaction.atomic():
            r = Review.objects.select_for_update().filter(report_no=number).first()
            if r is None:
                raise CommandError("그 번호의 후기를 찾지 못했습니다.")
            ReviewAction.objects.create(review=r, report_no=number, action=action, reason=reason, note=note, by=o["by"].strip()[:40] or "운영자")
            if action == ReviewAction.Action.DELETE:
                r.delete()
            elif action == ReviewAction.Action.HIDE:
                Review.objects.filter(pk=r.pk).update(is_hidden=True, hidden_reason=reason)
            elif action == ReviewAction.Action.SHOW:  # 내린 까닭은 지우지 않는다
                Review.objects.filter(pk=r.pk).update(is_hidden=False)
            else:
                state = Review.TextState.OK if action == ReviewAction.Action.TEXT_OK else Review.TextState.NO
                Review.objects.filter(pk=r.pk).update(text_state=state)
        self.stdout.write(f"{number}: {ReviewAction.Action(action).label}")

    def add(self, o):
        p = Partner(name=o["name"].strip(), cat=o["cat"], area=o["area"].strip(), desc=o["desc"].strip(),
                    phone=o["phone"].strip(), url=o["url"].strip(), tags=o["tags"].strip(), is_public=o["public"])
        try:
            p.full_clean(exclude=["slug"])
        except ValidationError as e:
            raise CommandError(f"값을 확인해 주세요: {', '.join(e.message_dict)}")
        p.save()
        self.stdout.write(f"{p.slug}: {'공개' if p.is_public else '비공개'}로 등록했습니다.")
