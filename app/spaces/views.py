"""B6 동학년 만들기: 학교 찾기(NEIS) → 학년·반 → 동학년 생성 → 동학년 화면.

동작은 prototype/grade.html, 모습은 design/start.html(2·3단계)·design/grade.html.
고른 학교는 세션에만 둔다. 학교 값은 서버가 찾은 검색 결과에서만 가져온다(브라우저가 보낸 이름은 믿지 않는다).
"""
import unicodedata

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.views import home as login_home

from . import neis
from .models import Member, School, Space

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
    """익명: 구글로 시작하기. 로그인: 내 동학년이 있으면 그 화면, 없으면 학교 찾기."""
    if not request.user.is_authenticated:
        return login_home(request)
    mine = _my_space(request.user)
    if mine:
        return redirect(f"/s/{mine.token}/")

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
            return redirect(f"/s/{space.token}/")
        request.session[PICKED] = school

    ctx = {
        "school": school, "grade": grade, "grades": GRADES, "classes": classes, "from_neis": from_neis,
        "n_classes": len(classes) - 1, "cls": cls, "name": name, "errors": errors,
    }
    if request.headers.get("HX-Request"):
        return render(request, "spaces/_class_form.html", ctx)
    return render(request, "spaces/start_class.html", ctx, status=400 if errors else 200)


@require_GET
def grade(request):
    """동학년 화면(만든 직후 상태까지). 소속 확인은 SpaceMiddleware가 한다."""
    space, me = request.space, request.member
    members = list(Member.objects.in_space(space).order_by("joined_at", "pk"))
    names = neis.class_names(space.school.neis_atpt, space.school.neis_code, space.grade)
    slots = [f"{space.grade}-{n}" for n in names] + ([f"{space.grade}-전담"] if names else [])
    taken = {m.cls for m in members}
    rows = []
    for i, m in enumerate(members):
        color, ink = PEOPLE_COLORS[i % len(PEOPLE_COLORS)]
        if i == 0:
            via = "구글 가입 · 만든 사람"
        else:
            via = "구글 가입" if m.user_id else "초대 링크 · 이름만"
        rows.append({"m": m, "label": _cls_label(m.cls), "color": color, "ink": ink, "via": via,
                     "at": timezone.localtime(m.joined_at), "me": m.pk == me.pk})
    waiting = [_cls_label(c) for c in slots if c not in taken]
    # prototype: 칸(NEIS 반 + 전담) 중 채워진 칸. 칸 밖 반은 칸을 하나씩 더한다. NEIS가 없으면 들어온 사람 수.
    extras = taken - set(slots)
    if slots:
        filled, total = len(taken & set(slots)) + len(extras), len(slots) + len(extras)
    else:
        filled = total = len(members)
    mine = next(r for r in rows if r["me"])
    return render(request, "spaces/grade.html", {
        "space": space, "me": me, "rows": rows, "waiting": waiting, "creator": members[0],
        "filled": filled, "total": total, "pct": round(filled / total * 100) if total else 0,
        "my_color": mine["color"], "my_ink": mine["ink"], "my_label": mine["label"],
    })
