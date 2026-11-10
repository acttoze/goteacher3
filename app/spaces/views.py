"""B6 동학년 만들기: 학교 찾기(NEIS) → 학년·반 → 동학년 생성 → 동학년 화면.

동작은 prototype/grade.html, 모습은 design/start.html(2·3단계)·design/grade.html.
고른 학교는 세션에만 둔다. 학교 값은 서버가 찾은 검색 결과에서만 가져온다(브라우저가 보낸 이름은 믿지 않는다).
"""
import unicodedata
from datetime import timedelta

import segno

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe

from accounts.models import DEVICE_AGE, DEVICE_COOKIE, GUEST_ORIGIN, DeviceToken, is_guest, is_id_member, new_guest
from accounts.views import home as login_home
from accounts.views import remember_pc, wants_remember
from collect.models import Collection, Notification
from config.ratelimit import allow, client_ip, too_many
from library.models import FormDoc

from . import neis
from .merge import retry_once, vacate
from .models import (
    INVITE,
    Member,
    MemberLog,
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

HITS = "new_school_hits"  # 마지막 검색 결과(서버가 NEIS에서 받은 것)
PICKED = "new_school"  # 고른 학교
GRADES = range(1, 7)
FALLBACK_CLASSES = ["1", "2", "3", "4"]  # NEIS 학급 정보가 없을 때(prototype: classes || 4)
# 초대 입장 POST: 가짜 이름으로 상한(반 수×2)을 채우지 않게. 학교는 공인 IP 하나를 같이 쓰므로 IP 쪽을 넉넉히.
JOIN_PER_MIN_IP = 10
JOIN_PER_MIN_SESSION = 5
ORG_NONE = "미배정"  # 기관 회원의 cls: 그룹에 배정되기 전(T1). 배정은 T2에서 관리자가 한다
SCHOOL_SEATS = 300  # 학교 공간 입장 상한. 큰 학교 교직원(200명 안팎)을 넉넉히 받는다
LAST_MANAGER = "마지막 관리자는 나갈 수 없어요. 다른 관리자를 먼저 세워 주세요."
NEIS_PER_MIN = 20  # 학교 검색(사용자당 분당)
NEIS_DOWN = "학교 정보를 불러오지 못했어요. 잠시 뒤 다시 찾아 주세요."
# T1 기관이 고르는 시도교육청(NEIS 시도교육청 코드). 기관 행의 neis_atpt에 넣는다.
OFFICES = [
    ("B10", "서울특별시교육청"), ("C10", "부산광역시교육청"), ("D10", "대구광역시교육청"), ("E10", "인천광역시교육청"),
    ("F10", "광주광역시교육청"), ("G10", "대전광역시교육청"), ("H10", "울산광역시교육청"), ("I10", "세종특별자치시교육청"),
    ("J10", "경기도교육청"), ("K10", "강원특별자치도교육청"), ("M10", "충청북도교육청"), ("N10", "충청남도교육청"),
    ("P10", "전북특별자치도교육청"), ("Q10", "전라남도교육청"), ("R10", "경상북도교육청"), ("S10", "경상남도교육청"),
    ("T10", "제주특별자치도교육청"),
]
# 사칭 방지: 기관 이름이 시도교육청 이름 그 자체면 받지 않는다(공백 무시). 정식 이름 + 흔한 줄임('경기교육청'·'서울시교육청')
_REGIONS = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
OFFICE_NAMES = ({label for _, label in OFFICES} | {"강원도교육청", "전라북도교육청"}
                | {f"{r}{x}교육청" for r in _REGIONS for x in ("", "시", "도", "특별시", "광역시", "특별자치시", "특별자치도")})
OFFICE_ONLY = "시도교육청 이름만으로는 만들 수 없어요. 부서 이름까지 써 주세요. 예: 인천광역시교육청 학교마을협력과"
UNCONFIRMED = "체험 중 · 운영자 미확인 기관"
TRIAL_USED = "이 계정은 이미 기관 체험을 썼어요. 계속 쓰려면 기관 플랜을 신청해 주세요."
# design/assets/people.js의 사람 색. 들어온 순서대로 돌려 쓴다. (색, 글자를 검게)
PEOPLE_COLORS = [("#c2410c", False), ("#0b7d7f", False), ("#d6246e", False), ("#37b24d", True), ("#f2a300", True)]
# prototype TR.SUPPORT_ROLES·TR.groupRoles. 학년 그룹은 직책 대신 반·전담을 고른다.
SUPPORT_ROLES = ["교장", "보건", "사서", "전문상담", "특수", "돌봄전담사"]
GROUP_ROLES = {
    "office": ["교감", "교무부장", "교무", "교무실무사", "행정원", *SUPPORT_ROLES],
    "admin": ["실장", "차장", "계장", "주무관"],
    "meal": ["영양교사", "조리사", "조리실무사"],
    "support": SUPPORT_ROLES,
}


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


def read_org(data):
    """기관 칸 읽기(시작 화면 기관 만들기·플랜 신청이 같이 쓴다): (값, 오류). 시도는 목록 안의 코드만 받는다."""
    values = {"atpt": data.get("atpt", ""), "org": _clean_name(data.get("org"))}
    errors = {}
    if values["atpt"] not in dict(OFFICES):
        errors["atpt"] = "시도교육청을 골라 주세요."
    if not values["org"]:
        errors["org"] = "기관 이름을 써 주세요. 예: 인천광역시교육청 학교마을협력과"
    elif "".join(values["org"].split()) in OFFICE_NAMES:
        errors["org"] = OFFICE_ONLY
    return values, errors


@never_cache
@require_safe
def home(request):
    """익명: 소개 페이지. 손님: 시작 화면(구글·아이디). 로그인: 내 동학년이 있으면 그 오늘 화면, 없으면 학교 찾기.
    ?kind=org: 학교 대신 기관(교육청·지원청 부서 등) 만들기(T1)."""
    mine = _my_space(request.user) if request.user.is_authenticated else None
    if mine:  # design: 로그인 뒤 첫 화면은 오늘(brand → today.html)
        return redirect(f"/s/{mine.token}/today")
    if not request.user.is_authenticated:  # 로그인 전: 소개 페이지(G5). 시작 화면은 /start
        return render(request, "landing.html", {"forms_n": FormDoc.objects.count()})
    if is_guest(request.user):  # 동학년 만들기는 계정 회원만
        return login_home(request)

    if request.GET.get("kind") == "org":
        return _org_form(request, {"name": request.user.first_name})
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
            ctx.update(schools=schools, total=total, more=total > len(schools), josa=_josa(q, "으로", "로"),
                       error=NEIS_DOWN if neis.failed(schools) else "")
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


def _org_form(request, values, errors=None, status=200):
    return render(request, "spaces/start_school.html",
                  {"kind": "org", "offices": OFFICES, "values": values, "errors": errors or {}, "trial_days": settings.ORG_TRIAL_DAYS,
                   "trial_used": TRIAL_USED if School.objects.filter(trial_by=request.user).exists() else ""}, status=status)


@never_cache
@login_required
@require_POST
def new_org(request):
    """기관 공간 바로 열기(T1, 사용자 결정 11/10): 고른 사람이 첫 관리자, 체험 기간(ORG_TRIAL_DAYS)만큼 플랜 기한.
    계정당 체험 기관 하나(School.trial_by). 이미 공간이 있는 사람은 그 공간으로(교사 한 명은 공간 하나)."""
    if is_guest(request.user):
        return redirect("/")
    values, errors = read_org(request.POST)
    values["name"] = _clean_name(request.POST.get("name"))
    values["position"] = _clean_name(request.POST.get("position"))
    if not values["name"]:
        errors["name"] = "함께 쓰는 분들에게 보일 이름을 써 주세요."
    if errors:
        return _org_form(request, values, errors, status=400)
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()  # 두 번 클릭·동학년 만들기와 직렬화
        mine = _my_space(request.user)
        if mine:
            return redirect(f"/s/{mine.token}/")
        if School.objects.filter(trial_by=request.user).exists():
            return _org_form(request, values, status=409)
        school = School.objects.create(neis_atpt=values["atpt"], neis_code="", name=values["org"], plan=School.Plan.SCHOOL,
                                       paid_until=timezone.localdate() + timedelta(days=settings.ORG_TRIAL_DAYS),
                                       trial_by=request.user)
        space = Space.objects.create(school=school, kind=Space.Kind.ORG, grade=0, name=values["org"])
        Member.objects.create(space=space, user=request.user, name=values["name"], cls=ORG_NONE, position=values["position"],
                              is_manager=True)
    resp = redirect(f"/s/{space.token}/settings?a=space")  # 첫 할 일은 초대
    resp.status_code = 303
    return resp


GRADE_TAKEN = "이 학년 동학년이 이미 있어요. 학년 대표 선생님께 초대 링크를 받아 들어오세요."
SCHOOL_TAKEN = "이 학교는 학교 공간을 씁니다. 담당 선생님께 학교 링크를 받아 주세요."


def _grade_taken(school, grade):
    """같은 학교·학년 동학년이 이미 있나(사용자 결정 11/3: 하나만). 만든 사람 이름은 알려 주지 않는다."""
    return Space.objects.filter(school__neis_atpt=school["atpt"], school__neis_code=school["code"], grade=grade).exists()


def _school_open(school):
    """이 학교에 학교 공간이 있나(플랜을 끈 뒤에도 합친 공간은 남으니 새 동학년을 만들지 않는다)."""
    return Space.objects.filter(school__neis_atpt=school["atpt"], school__neis_code=school["code"],
                                kind=Space.Kind.SCHOOL).exists()


def _school_notice(request, school):
    """학교 공간이 있는 학교: 만들기 양식 대신 안내만."""
    if request.method == "POST":
        request.session[PICKED] = school
    tpl = "spaces/_class_form.html" if request.headers.get("HX-Request") else "spaces/start_class.html"
    return render(request, tpl, {"school": school, "school_taken": SCHOOL_TAKEN},
                  status=409 if request.method == "POST" else 200)


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
    if not school or not school.get("code"):  # NEIS 코드 없는 학교(= 기관)에는 동학년을 만들지 않는다
        return redirect("/")
    if _school_open(school):
        return _school_notice(request, school)
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
                    School.objects.select_for_update().filter(pk=sch.pk).first()  # 플랜 켜기(open_school)와 직렬화
                    if _school_open(school):
                        return _school_notice(request, school)
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
    """[(반, 표시)]. 학교 공간은 모든 학년 + 다른 회원이 이미 쓰는 학년 반(교무실 등 학년 밖 값은 고르는 칸이 아니다). 기관은 반이 없다."""
    if space.is_org:
        return []
    if not space.is_school:
        return [(c, _cls_label(c)) for c in _grade_choices(space.school, space.grade)]
    slots = [c for g in GRADES for c in _grade_choices(space.school, g)]
    used = {c for c in Member.objects.in_space(space).values_list("cls", flat=True).distinct() if cls_group(c)}
    slots += sorted(used - set(slots))
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
        account = m.user_id and not is_guest(m.user)
        id_member = account and is_id_member(m.user)
        via = ("아이디 가입" if id_member else "구글 가입") if account else "초대 링크 · 이름만"
        if space.is_school and m.is_manager:
            via += " · 관리자"
        elif not space.is_school and i == 0:
            via += " · 만든 사람"
        rows.append({"m": m, "label": m.cls_text if space.is_school else _cls_label(m.cls), "color": color, "ink": ink, "via": via, "guest": not account, "id_member": id_member,
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
    """초대 링크·QR·쪽지 문구. 학교 공간은 관리자에게만 준다(명부·공지가 있는 공간이라 회원마다 퍼뜨리지 않게)."""
    if space.is_school and not me.is_manager:
        return {}
    link = f"{settings.SITE_URL}/j/{space.invite_token}"
    if space.is_org:
        room, how = f"{space.name} 기관 공간", "아래 링크를 누르고 구글이나 아이디로 로그인한 뒤 이름만 쓰면 들어와요. 그룹은 관리자가 정해요."
        if space.school.org_unconfirmed:  # 운영자가 켜기 전: 받는 사람이 진짜 기관인지 알 수 있게(T1)
            room += f"({UNCONFIRMED})"
    elif space.is_school:
        room, how = f"{space.school.name} 학교 공간", "아래 링크를 누르고 구글이나 아이디로 로그인한 뒤 소속·직책·이름만 고르면 들어와요."
    else:
        room, how = f"{space.school.name} {space.grade}학년 동학년방", "아래 링크를 누르고 반·이름만 고르면 들어와요. (가입 없음, 30초)"
    msg = (f"[고티처] {room}이에요.\n"
           f"{how}\n{link}\n\n"
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
    if ctx["is_creator"]:  # 내보내기 확인창: 그 선생님이 만든 취합은 만든 사람이 넘겨받는다(remove_member)
        made = dict(Collection.objects.in_space(space).filter(created_by__isnull=False)
                    .values_list("created_by").annotate(n=Count("pk")))
        for r in ctx["rows"]:
            r["made"] = made.get(r["m"].pk, 0)
    return ctx


@never_cache
@require_GET
def grade(request):
    """동학년 화면. 소속 확인은 SpaceMiddleware가 한다. 들어온 선생님은 5초마다 members로 다시 받는다.
    학교 공간은 명부가 길어 이 화면 대신 카드 격자인 학교 화면으로 보낸다(H3)."""
    if request.space.is_org:  # 기관은 학교 화면이 없다(T1)
        return redirect(f"/s/{request.space.token}/today")
    if request.space.is_school:
        return redirect(f"/s/{request.space.token}/school")
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

    낸 값은 남는다(줄은 member가 비고 합본에 그대로). 다음 취합 명부에서 빠진다.
    계정 회원은 이 동학년 자리만 빠지고 계정·로그인은 그대로(다른 동학년을 만들 수 있게).
    """
    m = get_in_space_or_404(Member, request, key=key)
    ctx = _space_ctx(request)
    if not ctx["is_creator"] or m.pk == request.member.pk:
        return HttpResponseForbidden()
    label = f"{_cls_label(m.cls)} {m.name}"
    seat_pk = m.pk

    def work():
        Space.objects.select_for_update(no_key=True).filter(pk=request.space.pk).first()  # 관리자 둘이 서로 내보낼 때 직렬화
        seat = Member.objects.in_space(request.space).filter(pk=seat_pk).select_related("user").first()
        if seat is None:  # 그사이 다른 관리자가 내보냈다
            return None
        if is_last_manager(seat):
            return HttpResponse(LAST_MANAGER, status=409)
        user = seat.user
        # 내보낸 회원이 만든 취합은 내보낸 사람(대표)이 넘겨받는다(마감·잠그기·링크 재발급·지우기)
        Collection.objects.in_space(request.space).filter(created_by=seat).update(created_by=request.member)
        seat.delete()
        if user and is_guest(user) and not Member.objects.filter(user=user).exists():
            user.delete()  # 기기 토큰 CASCADE, 세션은 사용자가 없어 무효
        return None

    refused = retry_once(work)
    if refused:
        return refused
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/")
    toast = f"{label} 선생님을 내보냈어요. 다시 못 들어오게 하려면 초대 링크를 새로 만드세요."
    if request.space.is_school:
        from .school import school_body  # school.py가 이 모듈을 읽는다
        return school_body(request, toast=toast)
    return render(request, "spaces/_roster.html", _space_ctx(request, toast=toast))


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
        defaults={"link": f"/s/{space.token}/", "about": member},
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
    """초대로 들어온 직후(design/join.html done): 함께 쓰는 선생님 + 구글·아이디 잇기(선택)."""
    return render(request, "spaces/welcome.html", _space_ctx(request, guest=is_guest(request.user), id_member=is_id_member(request.user)))


def _retired_invite(request, old):
    """합쳐진 옛 동학년 초대는 폐기(리드 결정). 회원이면 학교 공간으로, 아니면 안내만(입장·세션 저장 없음).
    입장 처리 중에 링크가 새로 바뀐 경우(old.merged_into 없음)도 안내만."""
    request.session.pop(INVITE, None)
    hub = old.merged_into
    if hub and request.user.is_authenticated and Member.objects.in_space(hub).filter(user=request.user).exists():
        return redirect(f"/s/{hub.token}/")
    return render(request, "spaces/join.html", {"retired": True, "reissued": hub is None})


def _lock_invite(request, space):
    """공간 행을 잠그고 다시 읽는다(동시 입장·합치기·재발급과 직렬화). 그사이 합쳐졌거나 링크가 바뀌었으면 None."""
    fresh = Space.objects.select_for_update(no_key=True).filter(pk=space.pk).first()
    if fresh is None or fresh.merged_into_id or fresh.invite_token != request.session.get(INVITE):
        return None
    return fresh


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
    """가입 없이 반·이름만 고르고 들어오기(prototype/join.html). 같은 반·같은 이름이면 같은 사람으로 본다.
    학교 공간은 계정 회원(구글·아이디)만 따로 들어온다(_join_school)."""
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
    if mine:  # 교사 한 명은 공간 하나
        ctx = {"space": space, "mine": mine}
        return render(request, "spaces/join.html", ctx, status=409 if request.method == "POST" else 200)
    if space.is_school:
        return _join_school(request, space)
    roster = _roster(space)
    picks = class_choices(space)
    slots = [c for c, _ in picks]
    seats = len(slots) * 2
    ctx = {"space": space, "creator": roster["creator"], "n": len(roster["rows"]), "mine": mine}

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
                if _lock_invite(request, space) is None:  # 같은 반·이름 동시 입장도 여기서 직렬화
                    return _retired_invite(request, Space.objects.filter(pk=space.pk).first() or space)
                same = Member.objects.in_space(space).filter(cls=cls, name=name).select_related("user").first()
                if same and same.user_id and not is_guest(same.user):
                    errors["name"] = "이 반·이름은 계정으로 이어 둔 선생님이에요. 본인이면 구글이나 아이디로 로그인해 주세요."
                elif same and user.is_authenticated:
                    # 손님 자리를 계정에 잇는 길은 그 손님 PC에서 「…으로 이어 두기」 하나뿐이다.
                    errors["name"] = ("이 반·이름으로 이미 들어온 선생님이 있어요. 본인이면 처음 들어온 PC에서 "
                                      "「구글 계정으로 이어 두기」나 「아이디로 이어 두기」를 눌러 주세요.")
                elif same and same.user_id:
                    user = same.user  # 같은 선생님이 다른 PC에서: 같은 손님으로 이어 준다
                    _seat_alert(space, same)
                elif Member.objects.in_space(space).count() >= seats:
                    errors["name"] = "이 동학년에는 더 들어올 수 없어요. 만든 선생님께 알려 주세요."
                else:
                    if not user.is_authenticated:
                        user = new_guest(name)
                        request.session[GUEST_ORIGIN] = user.pk  # 이 세션이 손님을 만들었다(계정 잇기 자격, P1 S5)
                    if same:  # 주인 없는 자리(예전 손님이 지워짐)
                        same.user = user
                        same.save(update_fields=["user"])
                    else:
                        Member.objects.create(space=space, user=user, name=name, cls=cls)
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


def _welcome_alert(space, seat):
    """새 회원이 들어오면 관리자들 🔔에(승인이 없으니 모르는 사람을 알아볼 수 있게). 웹 푸시 묶음에는 넣지 않는다."""
    text = f"{seat.name} 선생님({seat.cls_text})이 들어왔어요"
    Notification.objects.bulk_create(
        Notification(space=space, member=m, kind=Notification.Kind.JOIN, day=timezone.localdate(), text=text,
                     link=f"/s/{space.token}/" + ("channels" if space.is_org else "school"), pushed=True, about=seat)
        for m in Member.objects.in_space(space).filter(is_manager=True)
    )


def read_place(space, data, post):
    """학교 소속 칸 읽기(입장·내 그룹·직책 고치기가 같이 쓴다). post면 검증도 한다. (칸 값, 오류)."""
    group = data.get("group", "") if data.get("group") in Member.Group.values else ""
    cls, role, custom = data.get("cls", ""), data.get("role", ""), _clean_name(data.get("role_custom"))
    classes = roles = None
    if group[1:].isdigit():
        classes = [(c, _cls_label(c)) for c in _grade_choices(space.school, int(group[1:]))]
    elif group:
        roles = GROUP_ROLES[group]
    errors = {}
    if post:
        if not group:
            errors["group"] = "어디에서 일하시는지 골라 주세요."
        elif classes is not None and cls not in {c for c, _ in classes}:
            errors["cls"] = "몇 반인지 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
        elif roles is not None and not custom and role not in roles:
            errors["role"] = "직책을 고르거나 직접 써 주세요."
    return {"group": group, "classes": classes, "roles": roles, "cls": cls, "role": role, "custom": custom}, errors


def place_values(place):
    """검증한 칸 → (group, cls, position). 학년은 반이 곧 직책이고, 학년 밖은 소속 이름이 cls다."""
    if place["classes"] is not None:
        return place["group"], place["cls"], _cls_label(place["cls"])
    return place["group"], Member.Group(place["group"]).label, place["custom"] or place["role"]


def log_place(member, before):
    """소속·직책이 실제로 바뀌었으면 이력을 남긴다(본인이 고침). before: 고치기 전 cls_text."""
    if before != member.cls_text:
        MemberLog.objects.create(space_id=member.space_id, by=member.name, name=member.name, before=before,
                                 after=member.cls_text)


def _join_school(request, space):
    """학교 공간 입장(리드 결정, P에서 바뀜): 계정 회원(구글·아이디)만, 승인 없음. 소속·직책(학년이면 반)·이름을 고른다.
    계정 자리는 사람마다 하나라 같은 이름이어도 각자 들어온다. 익명·손님은 자리를 만들지 않고 로그인 칸을 보인다."""
    user = request.user
    post = request.method == "POST"
    ctx = {"space": space, "n": Member.objects.in_space(space).count()}
    if not space.school.writable:  # 리드 결정: 플랜이 끝나면 새 교직원만 막는다. 기존 회원은 위에서 공간으로 갔다
        ctx["expired"] = True
        return render(request, "spaces/join_school.html", ctx, status=403 if post else 200)
    if not user.is_authenticated or is_guest(user):
        ctx["guest"] = user.is_authenticated
        return render(request, "spaces/join_school.html", ctx, status=403 if post else 200)

    data = request.POST if post else request.GET
    if space.is_org:  # 기관: 이름(+직위)만. 그룹은 관리자가 배정한다(T1, 가입자는 미배정)
        place, errors = {"position": _clean_name(data.get("position"))}, {}
    else:
        place, errors = read_place(space, data, post)
    name = _clean_name(data.get("name", user.first_name))
    if post and not name:
        errors["name"] = "함께 쓰는 분들에게 보일 이름을 써 주세요." if space.is_org else "선생님들에게 보일 이름을 써 주세요."
    if post and not errors:
        group, cls, position = ("", ORG_NONE, place["position"]) if space.is_org else place_values(place)
        with transaction.atomic():
            get_user_model().objects.select_for_update().filter(pk=user.pk).first()  # 두 번 클릭·다른 공간 동시 입장
            mine = _my_space(user)
            if mine:
                return redirect(f"/s/{mine.token}/")
            if _lock_invite(request, space) is None:  # 상한 판단도 여기서 직렬화
                return _retired_invite(request, Space.objects.filter(pk=space.pk).first() or space)
            members = Member.objects.in_space(space)
            # 합칠 때 옮겨진 손님·주인 없는 자리와 겹치면 새 자리를 만들지 않는다(같은 사람이 둘로 갈리지 않게, 리드 결정)
            if members.filter(Q(user__isnull=True) | Q(user__username__startswith="guest:"), cls=cls, name=name).exists():
                errors["name"] = ("이 이름으로 이미 들어온 선생님이 있어요. 본인이면 처음 들어온 PC에서 "
                                  "「구글 계정으로 이어 두기」나 「아이디로 이어 두기」를 눌러 주세요.")
            elif members.count() >= SCHOOL_SEATS:
                errors["name"] = f"이 {space.word} 공간에는 더 들어올 수 없어요. 담당 선생님께 알려 주세요."
            else:
                seat = Member.objects.create(space=space, user=user, name=name, cls=cls, group=group, position=position)
                _welcome_alert(space, seat)
        if not errors:
            request.session.pop(INVITE, None)
            return redirect(f"/s/{space.token}/welcome")

    ctx.update(place, form=True, groups=Member.Group.choices, form_url="/join", name=name, errors=errors)
    tpl = "spaces/_join_school_form.html" if request.headers.get("HX-Request") else "spaces/join_school.html"
    return render(request, tpl, ctx, status=400 if errors else 200)


def _me_area(request, status=200, **extra):
    from accounts.device import _ctx, _this_pc  # 설정 화면 공통 문맥(이 PC 상태 포함)

    ctx = _ctx(request, _this_pc(request), area="me", classes=class_choices(request.space), **extra)
    return render(request, "accounts/_me.html", ctx, status=status)


@never_cache
@require_POST
def update_me(request):
    """설정 > 내 정보: 이름·반(design #me, 바꾸면 바로 저장). 둘 중 보낸 것만 바꾼다(이름 칸·반 단추가 따로 보낸다).

    같은 반·같은 이름 선생님이 이미 있으면 거부한다(초대 입장은 반·이름으로 같은 사람을 찾는다).
    이미 만든 취합의 줄은 그대로고, 다음 취합 명부부터 바뀐다.
    """
    me = request.member
    name = _clean_name(request.POST["name"]) if "name" in request.POST else me.name
    cls = request.POST.get("cls", me.cls)
    school = request.space.is_school
    error = ""
    if not name:
        error = "선생님들에게 보일 이름을 써 주세요." if school else "동학년에게 보일 이름을 써 주세요."
    elif cls != me.cls and cls not in {c for c, _ in class_choices(request.space)}:
        error = "반을 하나 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
    elif not school and Member.objects.in_space(request.space).filter(cls=cls, name=name).exclude(pk=me.pk).exists():
        error = "같은 반에 같은 이름 선생님이 이미 있어요. 이름 뒤에 글자를 붙여 구분해 주세요."
    if error:
        if not request.headers.get("HX-Request"):
            return redirect(f"/s/{request.space.token}/settings")
        return _me_area(request, status=400, error=error, typed=name)
    before, before_name = me.cls_text, me.name
    me.name, me.cls = name, cls
    # 보낸 칸만 저장한다(이름 칸 change와 반 단추가 거의 같이 갈 때 늦게 처리된 쪽이 반을 되돌리지 않게)
    fields = [f for f in ("name", "cls") if f in request.POST]
    if school and "cls" in fields and cls_group(cls):  # 학교 공간 학년 교사: 소속·직책이 반을 따라간다
        me.group, me.position = cls_group(cls), _cls_label(cls)
        fields += ["group", "position"]
    me.save(update_fields=fields)
    if school and "cls" in fields:
        log_place(me, before)
    if school and "name" in fields and before_name != name:  # H4: 이름은 줄 짝짓기·담당 표시에 쓰이므로 관리자가 알 수 있게
        MemberLog.objects.create(space=request.space, by=before_name, name=name, before=before_name, after=name)
    if not request.headers.get("HX-Request"):
        return redirect(f"/s/{request.space.token}/settings")
    return _me_area(request, saved=True)


@never_cache
@require_POST
def leave(request):
    """동학년 나가기(design #space). 동학년 이름을 써야 나간다.

    내가 만든 취합은 남은 선생님 중 가장 먼저 들어온 분이 넘겨받는다(그분이 새 학년 대표, _roster의 creator).
    학교 공간은 관리자가 넘겨받고(입장 순서로 넘기지 않음), 마지막 관리자는 나갈 수 없다.
    내가 낸 칸은 합본에 남는다(줄의 member만 빈다). 마지막 한 명이면 동학년을 지운다(같은 학년을 새로 만들 수 있게).
    손님은 다른 소속이 없으므로 계정도 지우고(기기 토큰 CASCADE) 로그아웃한다. 계정 회원은 첫 화면으로.
    """
    space, me = request.space, request.member
    if request.POST.get("confirm", "").strip() != space.name:
        return HttpResponseBadRequest(f"{space.word} 이름을 그대로 써 주세요.")
    space_pk, seat_pk, user_pk = space.pk, me.pk, request.user.pk

    def work():
        Space.objects.select_for_update(no_key=True).filter(pk=space_pk).first()  # 둘이 동시에 나갈 때 마지막 판단을 직렬화
        seat = Member.objects.filter(pk=seat_pk).select_related("space", "user").first()
        if seat is None:  # 두 번 눌렀다
            return "", False
        if is_last_manager(seat):
            return LAST_MANAGER, False
        user = seat.user
        vacate(seat)
        gone = is_guest(user) and not Member.objects.filter(user_id=user_pk).exists()
        if gone:
            user.delete()
        return "", gone

    refused, gone = retry_once(work)
    if refused:
        return HttpResponse(refused, status=409)
    response = redirect("/")
    if gone:
        logout(request)
        response.delete_cookie(DEVICE_COOKIE)
    return response
