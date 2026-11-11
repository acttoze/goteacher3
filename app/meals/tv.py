"""M2 급식 TV: /tv/<token> 로그인 없는 학생용 화면. 토큰을 아는 사람에게 **급식 정보만** 보여 준다.

- 회원·명부·공지·복무·일정을 읽지 않는다. 공간 화면용 컨텍스트(board_ctx·_school_ctx·_space_ctx)를 쓰지 않는 이유.
- 없는 토큰과 옛 토큰은 같은 404. 빈도 제한은 맞는 토큰은 토큰 단위로, 없는 토큰만 IP로 센다: 학교 전체가 IP 하나(NAT)라, 학생이 /tv/x를 많이 불러도 진짜 TV가 막히면 안 된다.
- 플랜이 끝난 학교는 학교 이름과 날짜만(학생에게 보이는 화면이라 플랜 이야기를 하지 않는다).
"""
from datetime import timedelta
from functools import wraps

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from config.csp import CSP
from config.ratelimit import allow, client_ip
from repairs.views import photo_response
from spaces import neis

from . import content
from .models import MealPhoto, MealSettings
from .views import WD, _day_or_404, _label, _week

PER_TOKEN = 300  # 분당. TV 한 대는 5분에 두어 번 부르므로 여러 대여도 넉넉하다
MISSES_PER_IP = 60  # 분당, 없는 토큰 요청만
LOOK_AHEAD = 60  # 방학이어도 다음 급식일을 찾는 날 수
FRAME_CSP = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")  # 급식 화면 '바로보기'의 같은 출처 iframe만 허용


def tv_view(view):
    """GET만, 캐시·색인 금지. 뷰에는 토큰으로 찾은 설정 행을 넘긴다."""
    @wraps(view)
    @never_cache
    @require_GET
    def inner(request, token, *args, **kwargs):
        row = MealSettings.objects.select_related("space__school").filter(tv_token=token).first()
        if row is None or not row.space.is_school:
            if allow("tv-miss", client_ip(request), MISSES_PER_IP, 60):
                raise Http404
            resp = HttpResponse(status=429)
        elif allow("tv", token, PER_TOKEN, 60):
            resp = view(request, row, *args, **kwargs)
        else:
            resp = HttpResponse(status=429)
        resp["X-Robots-Tag"] = "noindex, nofollow"
        return resp
    return inner


def _long_label(day):
    return f"{day.month}월 {day.day}일 {WD[day.weekday()]}요일"



def _meal(row):
    """NEIS 한 끼 → 화면에 쓰는 것만. 급식 인원은 넘기지 않는다."""
    dishes = row["dishes"]
    allergens = []
    for no, name in enumerate(content.ALLERGENS, 1):
        having = [d["name"] for d in dishes if no in d["allergy"]]
        if having:
            allergens.append({"no": no, "name": name, "dishes": having})
    return {
        "dishes": [{"name": d["name"], "allergy": d["allergy"], "self": d["self"]} for d in dishes],
        "kcal": row["kcal"],
        "nutr": [next(x for x in row["nutr"] if x["name"] == n) for n in content.NUTRIENTS if any(x["name"] == n for x in row["nutr"])],
        "origin": row["origin"],
        "allergens": allergens,
        "groups": [{"name": name, "dishes": [d["name"] for d in dishes if gid in content.groups_of(d["name"])]} for gid, name in content.GROUPS],
    }


def _next_meal(school, today, menus):
    """오늘 급식이 없을 때 다음 급식일. 이번 주 안에 없으면(방학) 더 멀리 한 번 더 본다."""
    key = f"{today:%Y%m%d}"
    later = sorted(y for y in menus if y > key)
    if not later:
        start, end = today + timedelta(days=1), today + timedelta(days=LOOK_AHEAD)
        rows = neis.meals(school.neis_atpt, school.neis_code, f"{start:%Y%m%d}", f"{end:%Y%m%d}")
        later = sorted(r["ymd"] for r in rows if r["dishes"] and r["ymd"] > key)
    if not later:
        return None
    return {"ymd": later[0], "label": _long_label(_day_or_404(later[0]))}


@tv_view
@xframe_options_sameorigin
def page(request, row):
    """화면 틀만. 내용은 data가 준다. 공통 컨텍스트(회원·알림)가 끼지 않게 request 없이 그린다. 틀 넣기 허용은 이 문서 하나뿐(사진·data는 그대로 none)."""
    resp = HttpResponse(render_to_string("meals/tv.html", {"base": f"/tv/{row.tv_token}"}))
    resp["Content-Security-Policy"] = FRAME_CSP
    return resp


@tv_view
def data(request, row):
    space, school, today = row.space, row.space.school, timezone.localdate()
    head = {"school": school.name, "date": f"{today:%Y%m%d}", "date_label": _long_label(today)}
    if not school.writable:
        return JsonResponse({**head, "quiet": True})
    days = _week(today)
    rows = neis.meals(school.neis_atpt, school.neis_code, f"{days[0]:%Y%m%d}", f"{days[-1]:%Y%m%d}")
    known = not neis.failed(rows)
    menus = {r["ymd"]: r for r in rows if r["dishes"]}
    shot = MealPhoto.objects.in_space(space).filter(date=today).exclude(photo="").first()
    hygiene = content.hygiene_of(today)
    return JsonResponse({
        **head,
        "known": known,  # False면 NEIS를 못 읽은 것. 화면은 받아 둔 메뉴를 그대로 쓰고 '없다'고 말하지 않는다
        "next": _next_meal(school, today, menus) if known and f"{today:%Y%m%d}" not in menus else None,
        "week_title": "다음 주 급식" if days[0] > today else "이번 주 급식",
        "days": [{"ymd": f"{d:%Y%m%d}", "label": _label(d), "meal": _meal(menus[f"{d:%Y%m%d}"]) if f"{d:%Y%m%d}" in menus else None} for d in days],
        "theme": row.theme,
        "hygiene": {"key": hygiene["key"], "title": hygiene["title"], "tag": hygiene["tag"], "items": hygiene["items"]},
        "slogan": content.slogan_of(today),
        "screens": [{"key": s["key"], "sec": s["sec"]} for s in content.screens(row.screens) if s["on"]],
        "photo": f"/tv/{row.tv_token}/photo/{today:%Y%m%d}?v={shot.version}" if shot else None,
    })


@tv_view
def photo(request, row, ymd):
    """그 학교 오늘 사진만. 화면이 오늘 것만 보여 주므로 다른 날은 토큰이 있어도 내주지 않는다."""
    if not row.space.school.writable or _day_or_404(ymd) != timezone.localdate():
        raise Http404
    return photo_response(get_object_or_404(MealPhoto.objects.in_space(row.space), date=timezone.localdate()))
