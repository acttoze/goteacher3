"""H1 학교 플랜 켜기: 학교 공간을 만들고 그 학교의 동학년을 합친다. 관리 명령 school_plan이 부른다.

공간을 가리키는 모델과 회원을 가리키는 모델은 Django 메타 정보로 찾는다(새 모델이 생겨도 빠지지 않게).
저장된 파일 경로(hwp/<옛 공간 번호>/…)는 그대로 두어도 DB에 저장된 이름으로 열린다.
"""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Value
from django.db.models.functions import Replace
from django.utils import timezone

from accounts.models import is_guest
from collect.models import Collection, Notification
from repairs.models import VendorAccount
from today.models import ALL, Event

from .models import Member, PlanRequest, School, Space, is_last_manager


def _fks_to(model):
    """model을 가리키는 FK 전부. related_name="+"(Event.by)도 빠지지 않게 숨은 것까지 본다."""
    return [f.field for f in model._meta.get_fields(include_hidden=True)
            if f.auto_created and not f.concrete and (f.one_to_many or f.one_to_one)]


def _one_seat_per_user(school, hub):
    """한 사용자가 여러 자리면(손님을 계정에 이으면 생긴다) 하나로. 학교 공간 자리, 먼저 들어온 자리 순으로 남긴다.

    지울 자리를 가리키는 값(만든 취합·줄·알림 등)은 남기는 자리로 옮긴다. 돌려주는 값: {지운 자리 pk: 남긴 자리 pk}.
    """
    seats = Member.objects.filter(space__school=school, user__isnull=False).order_by("joined_at", "pk")
    keep, gone = {}, {}
    for seat in sorted(seats, key=lambda m: m.space_id != hub.pk):
        first = keep.setdefault(seat.user_id, seat)
        if first is seat:
            continue
        for fk in _fks_to(Member):
            fk.model._base_manager.filter(**{fk.name: seat}).update(**{fk.name: first})
        pk = seat.pk
        seat.delete()
        gone[pk] = first.pk
    return gone


def _merge_one(old, hub):
    """옛 공간의 모든 것을 학교 공간으로. 이미 비었으면 아무것도 바뀌지 않는다."""
    Member.objects.filter(space=old, group="").update(group=f"g{old.grade}")
    Event.objects.filter(space=old, scope=ALL).update(scope=f"g{old.grade}")  # 옮기기 전에: 옛 일정은 그 학년에게만(H5)
    for fk in _fks_to(Space):
        if fk.model is not Space:
            fk.model._base_manager.filter(**{fk.name: old}).update(**{fk.name: hub})
    Notification.objects.filter(space=hub, link__startswith=f"/s/{old.token}/").update(
        link=Replace("link", Value(f"/s/{old.token}/"), Value(f"/s/{hub.token}/"))
    )
    Space.objects.filter(pk=old.pk).update(merged_into=hub)


def _seat_applicant(school, hub, req, identity_checked=False):
    """신청자(계정 회원)를 학교 공간 관리자로. 이 학교에 자리가 있으면 그 자리, 없으면 새 자리(소속은 교무실, 본인이 고칠 수 있다).
    다른 학교 공간·동학년에 있으면 ValueError(교사 한 명은 공간 하나).
    회원이 있는 학교에 회원 아닌 신청자를 세우려면 운영자가 재직을 확인했어야 한다(identity_checked). 아니면 ValueError."""
    if req.school_id != school.pk:
        raise ValueError("이 학교의 신청이 아닙니다.")
    user = req.user
    if user is None or is_guest(user):
        raise ValueError("신청자가 계정 회원이 아닙니다.")
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()  # 동학년 만들기·입장과 직렬화
    if Member.objects.filter(user=user).exclude(space=hub).exists():
        raise ValueError("신청자가 이미 다른 공간에 있습니다. 그 공간에서 나간 뒤 다시 켜 주세요.")
    members = Member.objects.in_space(hub)
    if not identity_checked and members.exists() and not members.filter(user=user).exists():
        raise ValueError("신청자가 이 학교 회원이 아닙니다. 공문·전화로 재직을 확인한 뒤 --identity-checked를 붙여 다시 켜 주세요.")
    seat, _ = Member.objects.get_or_create(space=hub, user=user, defaults={
        "name": req.by_name, "cls": Member.Group.OFFICE.label, "group": Member.Group.OFFICE, "position": req.position,
    })
    Member.objects.filter(pk=seat.pk).update(is_manager=True)


def open_school(school, until, manager=None, applicant=None, identity_checked=False):
    """학교 플랜을 켜고 합친다(한 트랜잭션). 다시 돌려도 결과가 같고, 그사이 옛 공간에 생긴 것까지 쓸어 온다.
    manager: 관리자로 세울 이 학교 회원. applicant: 첫 관리자로 세울 신청(PlanRequest).
    끊겼던 플랜을 다시 켜면 옛 업체 링크는 해제한다(재발급해야 한다). 켜져 있는 동안의 기한 연장은 그대로 둔다. TV 주소는 학교 공유 화면이라 유지.
    끝났을 때 학교에 관리자가 없으면 ValueError(모두 되돌린다). 이 학교의 대기 신청은 처리한 것으로 닫는다."""
    if school.is_org:  # 기관(NEIS 코드 없음)은 org_plan으로 켠다. 동학년 합치기는 학교에만(T1·T2)
        raise ValueError("기관은 학교 플랜으로 켤 수 없습니다. org_plan을 써 주세요.")
    with transaction.atomic():
        School.objects.select_for_update().filter(pk=school.pk).first()
        olds = list(Space.objects.select_for_update(no_key=True).filter(school=school, kind=Space.Kind.GRADE))
        if manager is not None:
            if not Member.objects.filter(pk=manager.pk, space__school=school).exists():
                raise ValueError("관리자는 이 학교의 회원이어야 합니다.")
            if manager.user_id is None or is_guest(manager.user):
                raise ValueError("관리자는 계정 회원만 될 수 있습니다.")
        resumed = not school.writable
        school.plan, school.paid_until = School.Plan.SCHOOL, until
        school.save(update_fields=["plan", "paid_until"])
        hub, _ = Space.objects.get_or_create(school=school, grade=0, defaults={"kind": Space.Kind.SCHOOL, "name": school.name})
        if resumed:
            VendorAccount.objects.in_space(hub).filter(revoked_at__isnull=True).update(revoked_at=timezone.now(), plan_cut=True)
        gone = _one_seat_per_user(school, hub)
        for old in olds:
            _merge_one(old, hub)
        if manager is not None:
            Member.objects.filter(pk=gone.get(manager.pk, manager.pk)).update(is_manager=True)
        if applicant is not None:
            _seat_applicant(school, hub, applicant, identity_checked)
        if not Member.objects.in_space(hub).filter(is_manager=True).exists():
            raise ValueError("이 학교에는 관리자가 없습니다. --manager=<회원 key> 또는 --manager-request=<신청 ID>로 첫 관리자를 정해 주세요.")
        PlanRequest.objects.filter(school=school, status=PlanRequest.Status.PENDING).update(
            status=PlanRequest.Status.DONE, done_at=timezone.now())
    return hub


def remove_manager(member):
    """관리자 해제. 마지막 관리자는 해제하지 않는다(ValueError)."""
    with transaction.atomic():
        Space.objects.select_for_update(no_key=True).filter(pk=member.space_id).first()
        if is_last_manager(member):
            raise ValueError("마지막 관리자는 해제할 수 없습니다. 다른 관리자를 먼저 세워 주세요.")
        Member.objects.filter(pk=member.pk).update(is_manager=False)


def vacate(seat):
    """자리 하나 비우기(나가기·계정 삭제). 부르는 쪽이 공간 행을 잠갔다. 남은 사람이 없으면 공간까지 지우고 True.
    동학년에서 그가 만든 취합은 가장 먼저 들어온 분이 넘겨받고(그분이 새 대표, lead_member), 학교 공간은 비워 둬 관리자가 넘겨받는다(collect.views._owner).
    자리를 지우므로 같은 반·이름으로 다시 들어와도 옛 자리의 대표 권한은 이어지지 않는다."""
    space = seat.space
    heir = Member.objects.in_space(space).exclude(pk=seat.pk).order_by("joined_at", "pk").first()
    if heir is None:
        space.delete()  # 취합·알림·한글 파일까지 CASCADE(디스크 파일은 E3 post_delete가 지운다)
        return True
    if not space.is_school:
        Collection.objects.in_space(space).filter(created_by=seat).update(created_by=heir)
    seat.delete()
    return False


def retry_once(work):
    """회원을 지우는 트랜잭션. 지우는 사이 끼어든 알림 줄은 커밋 때 FK 오류가 되므로 한 번 더 돌린다(그때는 그 줄까지 함께 지운다).
    work는 처음부터 다시 읽어야 한다: 지운 인스턴스는 pk가 비어 있다."""
    try:
        with transaction.atomic():
            return work()
    except IntegrityError:
        with transaction.atomic():
            return work()
