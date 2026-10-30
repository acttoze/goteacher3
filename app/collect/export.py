"""D4 내보내기 — 엑셀(CSV), 합본 표 복사(HTML 표 + TSV), 꼬리말 켜고 끄기. 동작은 prototype collectDetail(#csv·#cptbl·#foot).

- 표 모양은 prototype sheetOf: 앞 두 칸(unit_head) + 칸들. 여러 줄은 물품 줄마다(물품 없는 반은 뺌), 자동 계산은 계산값.
  줄·값은 합본 화면과 같은 계산(views._board)에서 가져온다.
- CSV는 UTF-8 BOM(엑셀 한글 깨짐 방지)·CRLF. =,+,-,@(와 탭·CR)로 시작하는 값 앞에 '를 붙여 엑셀이 수식으로 실행하지 않게 한다.
  xlsx는 새 의존성이 필요해 R1은 CSV(엑셀에서 바로 열림).
- 복사 HTML은 한글이 만든 클립보드 HTML(fixtures/paste)과 같은 꼴: table border=1·cellspacing=0, 칸마다 테두리·글꼴 인라인,
  병합 없음. 값은 모두 escape. 꼬리말은 파일에만 넣는다(prototype).
- 공간 소속은 SpaceMiddleware, 조회는 get_in_space_or_404. 제출 값은 로그에 남기지 않는다.
"""
import csv
import io
import re
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.html import escape
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from spaces.models import get_in_space_or_404

from .models import Collection
from .views import _board

FORMULA = ("=", "+", "-", "@", "\t", "\r")
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # 윈도 파일 이름에 못 쓰는 글자


def footer_text():
    return f"고티처로 모았습니다 · {urlparse(settings.SITE_URL).netloc}"


def sheet(c):
    """[머리글, 줄...] — 글자만. prototype sheetOf."""
    b = _board(c)
    head = [*(c.unit_head + ["받는 곳", "이름"][len(c.unit_head):])[:2], *(f.name for f in b["fields"])]
    rows = []
    for u in b["units"]:
        r = u["row"]
        for cells in u["lines"]:
            vals = [x["x"] for x in cells]
            if c.multi and not any(vals):  # prototype: 물품을 안 낸 반은 줄이 없다
                continue
            rows.append([r.unit, r.who, *vals])
    return [head, *rows]


def _one_line(v):
    return " ".join(str(v).split("\t")).replace("\r", " ").replace("\n", " ")


def to_tsv(lines):
    return "\n".join("\t".join(_one_line(v) for v in line) for line in lines)


TD = ('<td valign="middle" style="border:solid #000000 0.4pt;padding:1.4pt 5.1pt 1.4pt 5.1pt">'
      '<p style="margin:0;text-align:center;line-height:130%%"><span style="font-family:\'맑은 고딕\';font-size:10pt%s">%s</span></p></td>')


def to_html(lines):
    """한글 클립보드 형식을 닮은 단순 표. 머리글 줄은 굵게."""
    trs = "".join("<tr>" + "".join(TD % (";font-weight:bold" if i == 0 else "", escape(_one_line(v))) for v in line) + "</tr>"
                  for i, line in enumerate(lines))
    return ('<html><head><meta charset="utf-8"></head><body><!--StartFragment-->'
            '<table border="1" cellspacing="0" cellpadding="0" style="border-collapse:collapse;border:none">'
            f"{trs}</table><!--EndFragment--></body></html>")


def _safe(v):
    v = str(v)
    return "'" + v if v.startswith(FORMULA) else v


@never_cache
@require_GET
def export_csv(request, key):
    c = get_in_space_or_404(Collection, request, key=key)
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerows([_safe(v) for v in line] for line in sheet(c))
    if c.footer_on:
        w.writerow([footer_text()])
    r = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    name = BAD_NAME.sub("_", c.title).strip() or "모으기"
    r["Content-Disposition"] = content_disposition_header(True, f"{name}_합본.csv")
    r["X-Content-Type-Options"] = "nosniff"
    return r


@never_cache
@require_GET
def copy(request, key):
    """합본 표 복사(브라우저가 text/html·text/plain 둘 다 클립보드에 넣는다)."""
    c = get_in_space_or_404(Collection, request, key=key)
    lines = sheet(c)
    return JsonResponse({"html": to_html(lines), "tsv": to_tsv(lines)})


@require_POST
def footer(request, key):
    """꼬리말 켜고 끄기(htmx, 동학년 회원 누구나). 체크 상자 name=on."""
    c = get_in_space_or_404(Collection, request, key=key)
    c.footer_on = request.POST.get("on") == "on"
    c.save(update_fields=["footer_on"])
    toast = "내보내는 파일에 꼬리말을 넣어요." if c.footer_on else "꼬리말 없이 내보내요."
    return render(request, "collect/_footer.html", {"c": c, "space": request.space, "footer_toast": toast})
