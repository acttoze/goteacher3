"""B6 동학년 만들기: 학교 찾기(NEIS) → 학년·반 → 동학년 생성 → 동학년 화면.

동작은 prototype/grade.html, 모습은 design/start.html(2·3단계)·design/grade.html.
고른 학교는 세션에만 둔다. 학교 값은 서버가 찾은 검색 결과에서만 가져온다(브라우저가 보낸 이름은 믿지 않는다).
"""
import unicodedata

import segno

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import is_guest, new_guest
from accounts.views import home as login_home
from accounts.views import set_device_cookie

from . import neis
from .models import Member, School, Space, new_token

INVITE = "invite"  # 초대 링크로 연 공간의 invite_token(주소에서 빼서 세션에만)
HITS = "new_school_hits"  # 마지막 검색 결과(서버가 NEIS에서 받은 것)
PICKED = "new_school"  # 고른 학교
GRADES = range(1, 7)
FALLBACK_CLASSES = ["1", "2", "3", "4"]  # NEIS 학급 정보가 없을 때(prototype: classes || 4)
# design/assets/people.js의 사람 색. 들어온 순서대로 돌려 쓴다. (색, 글자를 검게)
PEOPLE_COLORS = [("#c2410c", False), ("#0b7d7f", False), ("#d6246e", False), ("#37b24d", True), ("#f2a300", True)]


def _josa(word, a, b):
    """받침 따라 조사(design shell.js의 U.josa와 같다)."""
    code = ord(word[-1]) - 0xAC00 if word else -1
    if not 0 <= code <= 11171:
        return b
    t = code % 28
    if a == "으로" and t == 8:
        return b
    return a if t else b


def _cls_label(cls):
    """'4-1' → '1반', '4-전담' → '전담'."""
    tail = cls.split("-", 1)[-1]
    return tail if not tail.isdigit() else f"{tail}반"


def _my_space(user):
    """내 동학년(가장 최근). 사용자 기준 조회라 공간 필터 대상이 아니다. R1: 교사 한 명은 동학년 하나."""
    mine = Member.objects.filter(user=user).select_related("space").order_by("-joined_at").first()
    return mine.space if mine else None


def _clean_name(raw):
    """제어 문자(Cc)·서식 문자(Cf: RTL/LTR override, U+200B 등 폭 없는 문자)·줄 구분자를 빼고 앞뒤 공백 정리."""
    s = "".join(ch for ch in str(raw or "") if unicodedata.category(ch) not in {"Cc", "Cf", "Zl", "Zp"})
    return s.strip()[:40]


@require_GET
def home(request):
    """익명: 소개 페이지. 손님: 구글로 시작하기. 로그인: 내 동학년이 있으면 그 화면, 없으면 학교 찾기."""
    mine = _my_space(request.user) if request.user.is_authenticated else None
    if mine:
        return redirect(f"/s/{mine.token}/")
    if not request.user.is_authenticated:  # 로그인 전: 소개 페이지(G5). 시작 화면은 /start
        return render(request, "landing.html")
    if is_guest(request.user):  # 동학년 만들기는 구글 회원만
        return login_home(request)

    q = request.GET.get("q", "").strip()[:40]
    ctx = {"q": q}
    if q:
        if len(q) < 2:
            ctx["error"] = "두 글자 이상 써 주세요."
        else:
            schools, total = neis.search_schools(q)
            request.session[HITS] = schools
            ctx.update(schools=schools, total=total, more=total > len(schools), josa=_josa(q, "으로", "로"))
    tpl = "spaces/_schools.html" if request.headers.get("HX-Request") else "spaces/start_school.html"
    return render(request, tpl, ctx)


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


def _int_grade(raw):
    try:
        g = int(raw)
    except (TypeError, ValueError):
        return 4
    return g if g in GRADES else 4


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

    if request.method == "POST":
        if cls not in {c for c, _ in classes}:
            errors["cls"] = "반을 하나 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
        if not name:
            errors["name"] = "동학년에게 보일 이름을 써 주세요."
        if not errors:
            with transaction.atomic():
                # 동시 POST: 사용자 행을 잠근 뒤 다시 확인한다(PostgreSQL에서 직렬화).
                get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()
                mine = _my_space(request.user)
                if mine:
                    return redirect(f"/s/{mine.token}/")
                sch, _ = School.objects.get_or_create(
                    neis_atpt=school["atpt"], neis_code=school["code"], defaults={"name": school["name"]}
                )
                space = Space.objects.create(
                    school=sch, grade=grade, name=f"{sch.name.replace('등학교', '')} {grade}학년"
                )
                Member.objects.create(space=space, user=request.user, name=name, cls=cls)
            request.session.pop(HITS, None)
            return redirect(f"/s/{space.token}/made")
        request.session[PICKED] = school

    ctx = {
        "school": school, "grade": grade, "grades": GRADES, "classes": classes, "from_neis": from_neis,
        "n_classes": len(classes) - 1, "cls": cls, "name": name, "errors": errors,
    }
    if request.headers.get("HX-Request"):
        return render(request, "spaces/_class_form.html", ctx)
    return render(request, "spaces/start_class.html", ctx, status=400 if errors else 200)


def _class_slots(space):
    """NEIS 반 + 전담. NEIS가 없으면 빈 목록."""
    names = neis.class_names(space.school.neis_atpt, space.school.neis_code, space.grade)
    return [f"{space.grade}-{n}" for n in names] + ([f"{space.grade}-전담"] if names else [])


def _roster(space, me=None):
    """들어온 선생님(들어온 순서, 사람 색)과 아직 안 들어온 칸."""
    members = list(Member.objects.in_space(space).select_related("user").order_by("joined_at", "pk"))
    slots = _class_slots(space)
    taken = {m.cls for m in members}
    rows = []
    for i, m in enumerate(members):
        color, ink = PEOPLE_COLORS[i % len(PEOPLE_COLORS)]
        google = m.user_id and not is_guest(m.user)
        via = "구글 가입" if google else "초대 링크 · 이름만"
        if i == 0:
            via += " · 만든 사람"
        rows.append({"m": m, "label": _cls_label(m.cls), "color": color, "ink": ink, "via": via,
                     "at": timezone.localtime(m.joined_at), "me": me is not None and m.pk == me.pk})
    waiting = [_cls_label(c) for c in slots if c not in taken]
    # prototype: 칸(NEIS 반 + 전담) 중 채워진 칸. 칸 밖 반은 칸을 하나씩 더한다. NEIS가 없으면 들어온 사람 수.
    extras = taken - set(slots)
    if slots:
        filled, total = len(taken & set(slots)) + len(extras), len(slots) + len(extras)
    else:
        filled = total = len(members)
    return {"rows": rows, "waiting": waiting, "slots": slots, "creator": members[0] if members else None,
            "filled": filled, "total": total, "pct": round(filled / total * 100) if total else 0}


def _invite(space, me):
    link = f"{settings.SITE_URL}/j/{space.invite_token}"
    msg = (f"[고티처] {space.school.name} {space.grade}학년 동학년방이에요.\n"
           f"아래 링크를 누르고 반·이름만 고르면 들어와요. (가입 없음, 30초)\n{link}\n\n"
           f"— {me.name} ({space.grade}학년 {_cls_label(me.cls)})")
    # QR은 서버에서 SVG로 만들어 data URI <img>로 넣는다(HTML을 그대로 끼워 넣지 않는다).
    qr = segno.make(link, error="m").svg_data_uri(border=0, dark="#0d0d0d", encode_minimal=True)
    return {"link": link, "msg": msg, "qr": qr}


def _space_ctx(request, **extra):
    """공간 화면 공통(shell.html): 들어온 선생님 + 내 색."""
    space, me = request.space, request.member
    ctx = {"space": space, "me": me, **_roster(space, me), **extra}
    mine = next(r for r in ctx["rows"] if r["me"])
    ctx.update(my_color=mine["color"], my_ink=mine["ink"], my_label=mine["label"])
    ctx["is_creator"] = ctx["creator"].pk == me.pk
    return ctx


@require_GET
def grade(request):
    """동학년 화면. 소속 확인은 SpaceMiddleware가 한다. 들어온 선생님은 5초마다 members로 다시 받는다."""
    ctx = _space_ctx(request)
    ctx.update(_invite(request.space, request.member))
    return render(request, "spaces/grade.html", ctx)


@require_GET
def members(request):
    """들어온 선생님 카드만(htmx 5초 폴링)."""
    return render(request, "spaces/_roster.html", _space_ctx(request))


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


@require_GET
def made(request):
    """동학년 만들기 4단계: 만들었어요 + 초대 링크(design/start.html step4)."""
    ctx = _space_ctx(request)
    ctx.update(_invite(request.space, request.member))
    return render(request, "spaces/made.html", ctx)


@require_GET
def welcome(request):
    """초대로 들어온 직후(design/join.html done): 함께 쓰는 선생님 + 구글 잇기(선택)."""
    return render(request, "spaces/welcome.html", _space_ctx(request, guest=is_guest(request.user)))


@require_GET
def invite(request, invite_token):
    """초대 링크는 자격 증명: 세션으로 옮기고 토큰 없는 주소로 보낸다(접근 기록·Referer에 덜 남게)."""
    if not Space.objects.filter(invite_token=invite_token).exists():
        return render(request, "spaces/join.html", {"missing": True}, status=404)
    request.session[INVITE] = invite_token
    return redirect("/join")


@require_http_methods(["GET", "POST"])
def join(request):
    """가입 없이 반·이름만 고르고 들어오기(prototype/join.html). 같은 반·같은 이름이면 같은 사람으로 본다."""
    space = Space.objects.filter(invite_token=request.session.get(INVITE) or "-").select_related("school").first()
    if space is None:
        return render(request, "spaces/join.html", {"missing": True}, status=404)
    user = request.user
    mine = _my_space(user) if user.is_authenticated else None
    if mine == space:
        request.session.pop(INVITE, None)
        return redirect(f"/s/{space.token}/")
    roster = _roster(space)
    slots = roster["slots"] or [f"{space.grade}-{n}" for n in FALLBACK_CLASSES] + [f"{space.grade}-전담"]
    ctx = {"space": space, "creator": roster["creator"], "n": len(roster["rows"]), "mine": mine}
    if mine:  # R1: 교사 한 명은 동학년 하나
        return render(request, "spaces/join.html", ctx, status=409 if request.method == "POST" else 200)

    data = request.POST if request.method == "POST" else {}
    cls = data.get("cls", "")
    name = _clean_name(data.get("name", user.first_name if user.is_authenticated else ""))
    errors = {}
    if request.method == "POST":
        if cls not in slots:
            errors["cls"] = "몇 반인지 골라 주세요. 반이 없는 교과 선생님은 「전담」이에요."
        if not name:
            errors["name"] = "동학년에게 보일 이름을 써 주세요."
        if not errors:
            with transaction.atomic():
                Space.objects.select_for_update().filter(pk=space.pk).first()  # 같은 반·이름 동시 입장 직렬화
                same = Member.objects.in_space(space).filter(cls=cls, name=name).select_related("user").first()
                if same and same.user_id and not is_guest(same.user):
                    errors["name"] = "이 반·이름은 구글 계정으로 이어 둔 선생님이에요. 본인이면 구글로 들어와 주세요."
                elif same and user.is_authenticated:
                    # 손님 자리를 구글에 잇는 길은 그 손님 PC에서 「구글 계정으로 이어 두기」 하나뿐이다.
                    errors["name"] = ("이 반·이름으로 이미 들어온 선생님이 있어요. 본인이면 처음 들어온 PC에서 "
                                      "「구글 계정으로 이어 두기」를 눌러 주세요.")
                elif same and same.user_id:
                    user = same.user  # 같은 선생님이 다른 PC에서: 같은 손님으로 이어 준다
                elif Member.objects.in_space(space).count() >= len(slots) * 2:
                    errors["name"] = "이 동학년에는 더 들어올 수 없어요. 만든 선생님께 알려 주세요."
                else:
                    if not user.is_authenticated:
                        user = new_guest(name)
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
                    set_device_cookie(request, response, user)
                return response

    by_cls = {}
    for r in roster["rows"]:
        by_cls.setdefault(r["m"].cls, r)
    ctx.update(errors=errors, cls=cls, name=name,
               choices=[{"cls": c, "label": _cls_label(c), "who": by_cls.get(c)} for c in slots])
    return render(request, "spaces/join.html", ctx, status=400 if errors else 200)
