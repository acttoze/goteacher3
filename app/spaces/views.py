"""B6 동학년 만들기: 학교 찾기(NEIS) → 학년·반 → 동학년 생성 → 동학년 화면.

동작은 prototype/grade.html, 모습은 design/start.html(2·3단계)·design/grade.html.
고른 학교는 세션에만 둔다. 학교 값은 서버가 찾은 검색 결과에서만 가져온다(브라우저가 보낸 이름은 믿지 않는다).
"""
import unicodedata

import segno

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import DEVICE_AGE, DEVICE_COOKIE, DeviceToken, is_guest, new_guest
from accounts.views import home as login_home
from accounts.views import remember_pc, wants_remember
from collect.models import Collection, Notification
from config.ratelimit import allow, client_ip, too_many
from library.models import FormDoc

from . import neis
from .models import (
    Member,
    School,
    Space,
    cls_group,
    cls_text,
    get_in_space_or_404,
    is_last_manager,
    lead_member,
    leads,
    new_token,
)

INVITE = "invite"  # 초대 링크로 연 공간의 invite_token(주소에서 빼서 세션에만)
HITS = "new_school_hits"  # 마지막 검색 결과(서버가 NEIS에서 받은 것)
PICKED = "new_school"  # 고른 학교
GRADES = range(1, 7)
FALLBACK_CLASSES = ["1", "2", "3", "4"]  # NEIS 학급 정보가 없을 때(prototype: classes || 4)
# 초대 입장 POST: 가짜 이름으로 상한(반 수×2)을 채우지 않게. 학교는 공인 IP 하나를 같이 쓰므로 IP 쪽을 넉넉히.
JOIN_PER_MIN_IP = 10
JOIN_PER_MIN_SESSION = 5
SCHOOL_SEATS = 300  # 학교 공간 입장 상한(H2 입장 화면 전까지 초대 링크 입장에 쓴다)
LAST_MANAGER = "마지막 관리자는 나갈 수 없어요. 다른 관리자를 먼저 세워 주세요."
NEIS_PER_MIN = 20  # 학교 검색(사용자당 분당)
# design/assets/people.js의 사람 색. 들어온 순서대로 돌려 쓴다. (색, 글자를 검게)
PEOPLE_COLORS = [("#c2410c", False), ("#0b7d7f", False), ("#d6246e", False), ("#37b24d", True), ("#f2a300", True)]


# 숫자를 읽은 소리의 받침(종성 번호, 0 = 없음). 일·칠·팔은 ㄹ(8): '으로' 대신 '로'.
_DIGIT_T = {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}
_PLACE_T = [(4, 4), (3, 4), (2, 1), (1, 17)]  # 만·천(ㄴ) · 백(ㄱ) · 십(ㅂ): 끝의 0 개수


def _final(word):
    """마지막 글자의 받침 번호. 한글이면 그 받침, 숫자로 끝나면 읽는 소리(13:10 → 십, 15:00 → 영). 그 밖은 None."""
    w = str(word).rstrip()
    if not w:
        return None
    code = ord(w[-1]) - 0xAC00
    if 0 <= code <= 11171:
        return code % 28
    if not w[-1].isdigit():
        return None
    digits = w[len(w.rstrip("0123456789")):]
    zeros = len(digits) - len(digits.rstrip("0"))
    if zeros == len(digits):  # 00·0 → 영
        return _DIGIT_T["0"]
    for n, t in _PLACE_T:
        if zeros >= n:
            return t
    return _DIGIT_T[digits[-1]]


def _josa(word, a, b):
    """받침 따라 조사(design shell.js의 U.josa). 숫자로 끝나면 읽는 소리 기준(리드 결정, G1)."""
    t = _final(word)
    if not t:
        return b
    if a == "으로" and t == 8:
        return b
    return a


def _cls_label(cls):
    """'4-1' → '1반', '4-전담' → '전담'."""
    tail = cls.split("-", 1)[-1]
    return tail if not tail.isdigit() else f"{tail}반"


def _my_space(user):
    """내 공간(가장 최근). 사용자 기준 조회라 공간 필터 대상이 아니다. 교사 한 명은 공간 하나."""
    mine = Member.objects.filter(user=user).select_related("space").order_by("-joined_at").first()
    return mine.space if mine else None


def _clean_name(raw):
    """제어 문자(Cc)·서식 문자(Cf: RTL/LTR override, U+200B 등 폭 없는 문자)·줄 구분자를 빼고 앞뒤 공백 정리."""
    s = "".join(ch for ch in str(raw or "") if unicodedata.category(ch) not in {"Cc", "Cf", "Zl", "Zp"})
    return s.strip()[:40]


@never_cache
@require_GET
def home(request):
    """익명: 소개 페이지. 손님: 구글로 시작하기. 로그인: 내 동학년이 있으면 그 오늘 화면, 없으면 학교 찾기."""
    mine = _my_space(request.user) if request.user.is_authenticated else None
    if mine:  # design: 로그인 뒤 첫 화면은 오늘(brand → today.html)
        return redirect(f"/s/{mine.token}/today")
    if not request.user.is_authenticated:  # 로그인 전: 소개 페이지(G5). 시작 화면은 /start
        return render(request, "landing.html", {"forms_n": FormDoc.objects.count()})
    if is_guest(request.user):  # 동학년 만들기는 구글 회원만
        return login_home(request)

    q = request.GET.get("q", "").strip()[:40]
    ctx = {"q": q}
    if q:
        if len(q) < 2:
            ctx["error"] = "두 글자 이상 써 주세요."
        elif too_many("neis", str(request.user.pk), NEIS_PER_MIN):  # NEIS 키 하루 한도를 한 사람이 다 쓰지 않게
            ctx["error"] = "검색을 너무 자주 했어요. 1분 뒤에 다시 찾아 주세요."
            ctx["limited"] = True
        else:
            schools, total = neis.search_schools(q)
            request.session[HITS] = schools
            ctx.update(schools=schools, total=total, more=total > len(schools), josa=_josa(q, "으로", "로"))
    tpl = "spaces/_schools.html" if request.headers.get("HX-Request") else "spaces/start_school.html"
    return render(request, tpl, ctx, status=429 if ctx.get("limited") else 200)


@never_cache
@login_required
@require_POST
def pick_school(request):
    if is_guest(request.user):
        return redirect("/")
    mine = _my_space(request.user)
    if mine:
        return redirect(f"/s/{mine.token}/")
    hits = request.session.get(HITS) or []
    try:
        i = int(request.POST.get("i", ""))
    except ValueError:
        return redirect("/")
    if not 0 <= i < len(hits):  # 음수 인덱스로 끝에서부터 고르지 않게
        return redirect("/")
    school = hits[i]
    request.session[PICKED] = school
    return redirect("/new/class")


GRADE_TAKEN = "이 학년 동학년이 이미 있어요. 학년 대표 선생님께 초대 링크를 받아 들어오세요."


def _grade_taken(school, grade):
    """같은 학교·학년 동학년이 이미 있나(사용자 결정 11/3: 하나만). 만든 사람 이름은 알려 주지 않는다.
    학교 공간(grade=0)이 있는 학교도 새 동학년을 만들지 않는다(H1: 입장 안내는 H2)."""
    return Space.objects.filter(school__neis_atpt=school["atpt"], school__neis_code=school["code"],
                                grade__in=[grade, 0]).exists()


def _int_grade(raw):
    try:
        g = int(raw)
    except (TypeError, ValueError):
        return 4
    return g if g in GRADES else 4


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def new_class(request):
    if is_guest(request.user):
        return redirect("/")
    mine = _my_space(request.user)
    if mine:
        request.session.pop(PICKED, None)
        return redirect(f"/s/{mine.token}/")
    # POST는 고른 학교를 먼저 꺼내 지운다(두 번 클릭). 입력 오류로 다시 보여 줄 때만 되돌린다.
    school = request.session.pop(PICKED, None) if request.method == "POST" else request.session.get(PICKED)
    if not school:
        return redirect("/")
    data = request.POST if request.method == "POST" else request.GET
    grade = _int_grade(data.get("grade"))
    names = neis.class_names(school["atpt"], school["code"], grade)
    from_neis = bool(names)
    classes = [(f"{grade}-{n}", _cls_label(f"{grade}-{n}")) for n in (names or FALLBACK_CLASSES)]
    classes.append((f"{grade}-전담", "전담"))
    cls = data.get("cls", "")
    name = _clean_name(data.get("name", request.user.first_name))
    errors = {}
    taken = _grade_taken(school, grade)

    if request.method == "POST" and not taken:
        if cls not in {c for c, _ in classes}:
            errors["cls"] = "반을 하나 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
        if not name:
            errors["name"] = "동학년에게 보일 이름을 써 주세요."
        if not errors:
            try:
                with transaction.atomic():
                    # 동시 POST: 사용자 행을 잠근 뒤 다시 확인한다(PostgreSQL에서 직렬화).
                    get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()
                    mine = _my_space(request.user)
                    if mine:
                        return redirect(f"/s/{mine.token}/")
                    sch, _ = School.objects.get_or_create(
                        neis_atpt=school["atpt"], neis_code=school["code"], defaults={"name": school["name"]}
                    )
                    space = Space.objects.create(  # 다른 선생님이 같은 학년을 동시에 만들면 유일 제약이 막는다
                        school=sch, grade=grade, name=f"{sch.name.replace('등학교', '')} {grade}학년"
                    )
                    Member.objects.create(space=space, user=request.user, name=name, cls=cls)
            except IntegrityError:
                taken = True
            else:
                request.session.pop(HITS, None)
                return redirect(f"/s/{space.token}/made")
    if request.method == "POST":
        request.session[PICKED] = school

    ctx = {
        "school": school, "grade": grade, "grades": GRADES, "classes": classes, "from_neis": from_neis,
        "n_classes": len(classes) - 1, "cls": cls, "name": name, "errors": errors,
        "taken": GRADE_TAKEN if taken else "",
    }
    if request.headers.get("HX-Request"):
        return render(request, "spaces/_class_form.html", ctx)
    status = 409 if taken and request.method == "POST" else 400 if errors else 200
    return render(request, "spaces/start_class.html", ctx, status=status)


def _grade_slots(school, grade):
    """NEIS 반 + 전담. NEIS가 없으면 빈 목록."""
    names = neis.class_names(school.neis_atpt, school.neis_code, grade)
    return [f"{grade}-{n}" for n in names] + ([f"{grade}-전담"] if names else [])


def _grade_choices(school, grade):
    """NEIS 반 + 전담. NEIS가 없으면 1~4반 + 전담(초대 입장과 같은 칸)."""
    return _grade_slots(school, grade) or [f"{grade}-{n}" for n in FALLBACK_CLASSES] + [f"{grade}-전담"]


def _class_slots(space):
    """명부에 보일 칸. 학교 공간은 칸 없이 들어온 사람만 센다(학교 명부는 H3)."""
    return [] if space.is_school else _grade_slots(space.school, space.grade)


def class_choices(space):
    """[(반, 표시)]. 학교 공간은 합쳐진 학년 + 회원이 이미 쓰는 반(입장 화면은 H2)."""
    if not space.is_school:
        return [(c, _cls_label(c)) for c in _grade_choices(space.school, space.grade)]
    grades = sorted(space.merged_spaces.values_list("grade", flat=True)) or list(GRADES)
    slots = [c for g in grades for c in _grade_choices(space.school, g)]
    used = Member.objects.in_space(space).values_list("cls", flat=True).distinct()
    slots += sorted(set(used) - set(slots))
    return [(c, cls_text(c)) for c in slots]


def _roster(space, me=None):
    """들어온 선생님(들어온 순서, 사람 색)과 아직 안 들어온 칸."""
    members = list(Member.objects.in_space(space).select_related("user").order_by("joined_at", "pk"))
    slots = _class_slots(space)
    taken = {m.cls for m in members}
    creator = lead_member(space)
    rows = []
    for i, m in enumerate(members):
        color, ink = PEOPLE_COLORS[i % len(PEOPLE_COLORS)]
        google = m.user_id and not is_guest(m.user)
        via = "구글 가입" if google else "초대 링크 · 이름만"
        if space.is_school and m.is_manager:
            via += " · 관리자"
        elif not space.is_school and i == 0:
            via += " · 만든 사람"
        rows.append({"m": m, "label": m.cls_text if space.is_school else _cls_label(m.cls), "color": color, "ink": ink, "via": via, "guest": not google,
                     "at": timezone.localtime(m.joined_at), "me": me is not None and m.pk == me.pk})
    waiting = [_cls_label(c) for c in slots if c not in taken]
    # prototype: 칸(NEIS 반 + 전담) 중 채워진 칸. 칸 밖 반은 칸을 하나씩 더한다. NEIS가 없으면 들어온 사람 수.
    extras = taken - set(slots)
    if slots:
        filled, total = len(taken & set(slots)) + len(extras), len(slots) + len(extras)
    else:
        filled = total = len(members)
    return {"rows": rows, "waiting": waiting, "slots": slots, "creator": creator,
            "filled": filled, "total": total, "pct": round(filled / total * 100) if total else 0}


def _invite(space, me):
    link = f"{settings.SITE_URL}/j/{space.invite_token}"
    room = f"{space.school.name} 학교 공간" if space.is_school else f"{space.school.name} {space.grade}학년 동학년방"
    msg = (f"[고티처] {room}이에요.\n"
           f"아래 링크를 누르고 반·이름만 고르면 들어와요. (가입 없음, 30초)\n{link}\n\n"
           f"— {me.name} ({me.cls_text})")
    # QR은 서버에서 SVG로 만들어 data URI <img>로 넣는다(HTML을 그대로 끼워 넣지 않는다).
    qr = segno.make(link, error="m").svg_data_uri(border=0, dark="#0d0d0d", encode_minimal=True)
    return {"link": link, "msg": msg, "qr": qr}


def _space_ctx(request, **extra):
    """공간 화면 공통(shell.html): 들어온 선생님 + 내 색."""
    space, me = request.space, request.member
    ctx = {"space": space, "me": me, **_roster(space, me), **extra}
    mine = next(r for r in ctx["rows"] if r["me"])
    ctx.update(my_color=mine["color"], my_ink=mine["ink"], my_label=mine["label"])
    ctx["is_creator"] = leads(space, me)
    ctx["last_manager"] = is_last_manager(me)
    if ctx["is_creator"]:  # 내보내기 확인창: 그 선생님이 만든 모으기는 만든 사람이 넘겨받는다(remove_member)
        made = dict(Collection.objects.in_space(space).filter(created_by__isnull=False)
                    .values_list("created_by").annotate(n=Count("pk")))
        for r in ctx["rows"]:
            r["made"] = made.get(r["m"].pk, 0)
    return ctx


@never_cache
@require_GET
def grade(request):
    """동학년 화면. 소속 확인은 SpaceMiddleware가 한다. 들어온 선생님은 5초마다 members로 다시 받는다."""
    ctx = _space_ctx(request)
    ctx.update(_invite(request.space, request.member))
    return render(request, "spaces/grade.html", ctx)


@never_cache
@require_GET
def members(request):
    """들어온 선생님 카드만(htmx 5초 폴링)."""
    return render(request, "spaces/_roster.html", _space_ctx(request))


@never_cache
@require_POST
def new_invite(request):
    """초대 링크 새로 만들기(만든 사람만). 예전 링크와 그 링크로 열어 둔 입장 화면은 바로 닫힌다."""
    ctx = _space_ctx(request)
    if not ctx["is_creator"]:
        return HttpResponseForbidden()
    space = request.space
    space.invite_token = new_token()
    space.save(update_fields=["invite_token"])
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{space.token}/")
    ctx.update(_invite(space, request.member), reissued=True)
    return render(request, "spaces/_invite.html", ctx)


@never_cache
@require_POST
def remove_member(request, key):
    """회원 내보내기(대표만, 나는 못 내보냄, 마지막 관리자도 못 내보냄). 손님이면 그 User를 지워 기기 토큰·세션도 끊는다.

    낸 값은 남는다(줄은 member가 비고 합본에 그대로). 다음 모으기 명부에서 빠진다.
    구글 회원은 이 동학년 자리만 빠지고 계정·로그인은 그대로(다른 동학년을 만들 수 있게).
    """
    m = get_in_space_or_404(Member, request, key=key)
    ctx = _space_ctx(request)
    if not ctx["is_creator"] or m.pk == request.member.pk:
        return HttpResponseForbidden()
    label = f"{_cls_label(m.cls)} {m.name}"
    with transaction.atomic():
        Space.objects.select_for_update().filter(pk=request.space.pk).first()  # 관리자 둘이 서로 내보낼 때 직렬화
        if is_last_manager(m):
            return HttpResponse(LAST_MANAGER, status=409)
        user = m.user
        # 내보낸 회원이 만든 모으기는 내보낸 사람(대표)이 넘겨받는다(마감·잠그기·링크 재발급·지우기)
        Collection.objects.in_space(request.space).filter(created_by=m).update(created_by=request.member)
        m.delete()
        if user and is_guest(user) and not Member.objects.filter(user=user).exists():
            user.delete()  # 기기 토큰 CASCADE, 세션은 사용자가 없어 무효
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/")
    ctx = _space_ctx(request, toast=f"{label} 선생님을 내보냈어요. 다시 못 들어오게 하려면 초대 링크를 새로 만드세요.")
    return render(request, "spaces/_roster.html", ctx)


def _seat_alert(space, member):
    """기기가 붙어 있는 손님 자리로 다른 브라우저가 들어왔다: 만든 사람 🔔에(허용형 입장은 그대로)."""
    if not DeviceToken.objects.filter(user=member.user, created_at__gt=timezone.now() - DEVICE_AGE).exists():
        return
    creator = lead_member(space)
    if creator is None or creator.pk == member.pk:
        return
    text = f"{_cls_label(member.cls)} {member.name} 자리로 새 기기가 들어왔어요"
    # 같은 자리·같은 날은 1건만(줄이 없어 유일 제약이 안 걸린다). 부르는 쪽이 공간 행을 잠가 둬 동시 입장도 하나
    Notification.objects.get_or_create(
        space=space, member=creator, kind=Notification.Kind.SEAT, day=timezone.localdate(), text=text,
        defaults={"link": f"/s/{space.token}/"},
    )


@never_cache
@require_GET
def made(request):
    """동학년 만들기 4단계: 만들었어요 + 초대 링크(design/start.html step4)."""
    if request.space.is_school:  # 합쳐지기 전에 받아 둔 주소
        return redirect(f"/s/{request.space.token}/")
    ctx = _space_ctx(request)
    ctx.update(_invite(request.space, request.member))
    return render(request, "spaces/made.html", ctx)


@never_cache
@require_GET
def welcome(request):
    """초대로 들어온 직후(design/join.html done): 함께 쓰는 선생님 + 구글 잇기(선택)."""
    return render(request, "spaces/welcome.html", _space_ctx(request, guest=is_guest(request.user)))


def _retired_invite(request, old):
    """합쳐진 옛 동학년 초대는 폐기(리드 결정). 회원이면 학교 공간으로, 아니면 안내만(입장·세션 저장 없음)."""
    request.session.pop(INVITE, None)
    hub = old.merged_into
    if request.user.is_authenticated and Member.objects.in_space(hub).filter(user=request.user).exists():
        return redirect(f"/s/{hub.token}/")
    return render(request, "spaces/join.html", {"retired": True})


@never_cache
@require_GET
def invite(request, invite_token):
    """초대 링크는 자격 증명: 세션으로 옮기고 토큰 없는 주소로 보낸다(접근 기록·Referer에 덜 남게)."""
    space = Space.objects.filter(invite_token=invite_token).first()
    if space is None:
        return render(request, "spaces/join.html", {"missing": True}, status=404)
    if space.merged_into_id:
        return _retired_invite(request, space)
    request.session[INVITE] = invite_token
    return redirect("/join")


@never_cache
@require_http_methods(["GET", "POST"])
def join(request):
    """가입 없이 반·이름만 고르고 들어오기(prototype/join.html). 동학년은 같은 반·같은 이름이면 같은 사람으로 본다.
    학교 공간은 그렇게 잇지 않는다(리드 결정: 이름만으로 자리를 차지하면 안 됨). 구글 필수 입장은 H2."""
    space = Space.objects.filter(invite_token=request.session.get(INVITE) or "-").select_related("school").first()
    if space is None:
        return render(request, "spaces/join.html", {"missing": True}, status=404)
    if space.merged_into_id:  # 합쳐지기 전에 링크를 열어 세션에 남아 있던 경우
        return _retired_invite(request, space)
    user = request.user
    mine = _my_space(user) if user.is_authenticated else None
    if mine == space:
        request.session.pop(INVITE, None)
        return redirect(f"/s/{space.token}/")
    roster = _roster(space)
    picks = class_choices(space)
    slots = [c for c, _ in picks]
    seats = SCHOOL_SEATS if space.is_school else len(slots) * 2
    ctx = {"space": space, "creator": roster["creator"], "n": len(roster["rows"]), "mine": mine}
    if mine:  # R1: 교사 한 명은 동학년 하나
        return render(request, "spaces/join.html", ctx, status=409 if request.method == "POST" else 200)

    data = request.POST if request.method == "POST" else {}
    cls = data.get("cls", "")
    name = _clean_name(data.get("name", user.first_name if user.is_authenticated else ""))
    remember = wants_remember(request.POST, False)  # '이 PC 기억하기(1년)'(G3 S2), 기본 끔(사용자 결정 11/3)
    errors = {}
    limited = request.method == "POST" and not (  # 둘 다 센다(& 는 앞이 거짓이어도 뒤를 부른다)
        allow("join-ip", client_ip(request), JOIN_PER_MIN_IP, 60)
        & allow("join-s", request.session.session_key or "", JOIN_PER_MIN_SESSION, 60)
    )
    if limited:
        errors["name"] = "잠깐 너무 자주 눌렀어요. 1분 뒤에 다시 해 주세요."
    elif request.method == "POST":
        if cls not in slots:
            errors["cls"] = "몇 반인지 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
        if not name:
            errors["name"] = "동학년에게 보일 이름을 써 주세요."
        if not errors:
            with transaction.atomic():
                Space.objects.select_for_update().filter(pk=space.pk).first()  # 같은 반·이름 동시 입장 직렬화
                same = Member.objects.in_space(space).filter(cls=cls, name=name).select_related("user").first()
                if same and space.is_school:  # 손님·빈 자리 포함 어떤 자리도 이 경로로 잇지 않는다
                    errors["name"] = ("이 반·이름은 이미 있어요. 본인이면 처음 들어온 PC에서 「구글 계정으로 이어 두기」를 "
                                      "누르거나 구글로 들어와 주세요.")
                elif same and same.user_id and not is_guest(same.user):
                    errors["name"] = "이 반·이름은 구글 계정으로 이어 둔 선생님이에요. 본인이면 구글로 들어와 주세요."
                elif same and user.is_authenticated:
                    # 손님 자리를 구글에 잇는 길은 그 손님 PC에서 「구글 계정으로 이어 두기」 하나뿐이다.
                    errors["name"] = ("이 반·이름으로 이미 들어온 선생님이 있어요. 본인이면 처음 들어온 PC에서 "
                                      "「구글 계정으로 이어 두기」를 눌러 주세요.")
                elif same and same.user_id:
                    user = same.user  # 같은 선생님이 다른 PC에서: 같은 손님으로 이어 준다
                    _seat_alert(space, same)
                elif Member.objects.in_space(space).count() >= seats:
                    errors["name"] = "이 동학년에는 더 들어올 수 없어요. 만든 선생님께 알려 주세요."
                else:
                    if not user.is_authenticated:
                        user = new_guest(name)
                    if same:  # 주인 없는 자리(예전 손님이 지워짐)
                        same.user = user
                        same.save(update_fields=["user"])
                    else:
                        Member.objects.create(space=space, user=user, name=name, cls=cls,
                                              group=cls_group(cls) if space.is_school else "")
            if not errors:
                request.session.pop(INVITE, None)
                response = redirect(f"/s/{space.token}/welcome")
                if not request.user.is_authenticated:
                    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                    remember_pc(request, response, user, remember)
                return response

    by_cls = {}
    for r in roster["rows"]:
        by_cls.setdefault(r["m"].cls, r)
    ctx.update(errors=errors, cls=cls, name=name, remember=remember,
               choices=[{"cls": c, "label": label, "who": by_cls.get(c)} for c, label in picks])
    return render(request, "spaces/join.html", ctx, status=(429 if limited else 400) if errors else 200)


def _me_area(request, status=200, **extra):
    from accounts.device import _ctx, _this_pc  # 설정 화면 공통 문맥(이 PC 상태 포함)

    ctx = _ctx(request, _this_pc(request), area="me", classes=class_choices(request.space), **extra)
    return render(request, "accounts/_me.html", ctx, status=status)


@never_cache
@require_POST
def update_me(request):
    """설정 > 내 정보: 이름·반(design #me, 바꾸면 바로 저장). 둘 중 보낸 것만 바꾼다(이름 칸·반 단추가 따로 보낸다).

    같은 반·같은 이름 선생님이 이미 있으면 거부한다(초대 입장은 반·이름으로 같은 사람을 찾는다).
    이미 만든 모으기의 줄은 그대로고, 다음 모으기 명부부터 바뀐다.
    """
    me = request.member
    name = _clean_name(request.POST["name"]) if "name" in request.POST else me.name
    cls = request.POST.get("cls", me.cls)
    error = ""
    if not name:
        error = "동학년에게 보일 이름을 써 주세요."
    elif cls not in {c for c, _ in class_choices(request.space)}:
        error = "반을 하나 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
    elif Member.objects.in_space(request.space).filter(cls=cls, name=name).exclude(pk=me.pk).exists():
        error = "같은 반에 같은 이름 선생님이 이미 있어요. 이름 뒤에 글자를 붙여 구분해 주세요."
    if error:
        if not request.headers.get("HX-Request"):
            return redirect(f"/s/{request.space.token}/settings")
        return _me_area(request, status=400, error=error, typed=name)
    me.name, me.cls = name, cls
    # 보낸 칸만 저장한다(이름 칸 change와 반 단추가 거의 같이 갈 때 늦게 처리된 쪽이 반을 되돌리지 않게)
    fields = [f for f in ("name", "cls") if f in request.POST]
    if request.space.is_school and "cls" in fields:
        me.group = cls_group(cls) or me.group
        fields.append("group")
    me.save(update_fields=fields)
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/settings")
    return _me_area(request, saved=True)


@never_cache
@require_POST
def leave(request):
    """동학년 나가기(design #space). 동학년 이름을 써야 나간다.

    내가 만든 모으기는 남은 선생님 중 가장 먼저 들어온 분이 넘겨받는다(그분이 새 학년 대표, _roster의 creator).
    학교 공간은 관리자가 넘겨받고(입장 순서로 넘기지 않음), 마지막 관리자는 나갈 수 없다.
    내가 낸 칸은 합본에 남는다(줄의 member만 빈다). 마지막 한 명이면 동학년을 지운다(같은 학년을 새로 만들 수 있게).
    손님은 다른 소속이 없으므로 계정도 지우고(기기 토큰 CASCADE) 로그아웃한다. 구글 회원은 첫 화면으로.
    """
    space, me = request.space, request.member
    if request.POST.get("confirm", "").strip() != space.name:
        return HttpResponseBadRequest("동학년 이름을 그대로 써 주세요.")
    user = request.user
    with transaction.atomic():
        Space.objects.select_for_update().filter(pk=space.pk).first()  # 둘이 동시에 나갈 때 마지막 판단을 직렬화
        if is_last_manager(me):
            return HttpResponse(LAST_MANAGER, status=409)
        heir = Member.objects.in_space(space).exclude(pk=me.pk).order_by("joined_at", "pk").first()
        if heir is None:
            space.delete()  # 모으기·알림·한글 파일까지 CASCADE(디스크 파일은 E3 post_delete가 지운다)
        else:
            if not space.is_school:  # 학교 공간은 비워 둬 관리자가 넘겨받는다(collect.views._owner)
                Collection.objects.in_space(space).filter(created_by=me).update(created_by=heir)
            me.delete()
        gone = is_guest(user) and not Member.objects.filter(user=user).exists()
        if gone:
            user.delete()
    response = redirect("/")
    if gone:
        logout(request)
        response.delete_cookie(DEVICE_COOKIE)
    return response
