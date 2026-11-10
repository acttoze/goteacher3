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
from decimal import Decimal
from urllib.parse import urlparse

import xlwt

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.html import escape
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .access import owner_view
from .models import Collection
from .presets import EDUFINE
from .views import _board

FORMULA = ("=", "+", "-", "@", "\t", "\r")
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # 윈도 파일 이름에 못 쓰는 글자


def footer_text():
    return f"고티처로 모았습니다 · {urlparse(settings.SITE_URL).netloc}"


def sheet(c):
    """[머리글, 줄...] — 글자만. prototype sheetOf."""
    b = _board(c)
    head = [*(c.head_shown + ["받는 곳", "이름"][len(c.unit_head):])[:2], *(f.name for f in b["fields"])]
    rows = []
    for u in b["units"]:
        r = u["row"]
        for cells in u["lines"]:
            vals = [x["x"] for x in cells]
            if c.multi and not any(vals):  # prototype: 물품을 안 낸 반은 줄이 없다
                continue
            rows.append([r.unit, u["by"] if r.group else r.who, *vals])  # 그룹 칸의 '작성' 칸은 실제로 낸 사람
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
@owner_view(Collection)
def export_csv(request, c):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerows([_safe(v) for v in line] for line in sheet(c))
    if c.footer_on:
        w.writerow([footer_text()])
    r = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    name = BAD_NAME.sub("_", c.title).strip() or "취합"
    r["Content-Disposition"] = content_disposition_header(True, f"{name}_합본.csv")
    r["X-Content-Type-Options"] = "nosniff"
    return r


def edufine_rows(c):
    """S1 에듀파인 줄: 합본 순서대로 물품마다 [내용, 규격, 단위, 수량, 예상단가]. 빈 줄은 뺀다. 받는 곳·주소·꼬리말은 넣지 않는다."""
    b = _board(c)
    at = [next((i for i, f in enumerate(b["fields"]) if f.name == name), None) for name in EDUFINE]
    out = []
    for u in b["units"]:
        for cells in u["lines"]:
            vals = [cells[i]["v"].strip() if i is not None else "" for i in at]
            if any(vals):
                out.append(vals)
    return out


XLS_NUM_MAX = Decimal(10) ** 15  # 엑셀이 정확히 다루는 자릿수 안만 숫자 칸으로


def _xls_num(v):
    """수량·예상단가는 숫자 칸으로(정수면 int). 숫자가 아니거나 너무 크면 글자 그대로(제출 때 막지만 옛 값 방어)."""
    try:
        n = Decimal(v.replace(",", ""))
        if not n.is_finite() or abs(n) >= XLS_NUM_MAX:
            return v
        return int(n) if n == n.to_integral_value() else float(n)
    except ArithmeticError:
        return v


def _edufine_styles():
    """품목내역(통합).xls(에듀파인 내려받기 양식) 머리글 서식: 돋움 9pt 흰 글자, 회색 바탕, 가운데·줄바꿈, 얇은 회색 테두리."""
    def style(head, left):
        st = xlwt.XFStyle()
        st.font.name, st.font.height = "Dotum", 180
        b = st.borders
        b.top = b.bottom = b.right = xlwt.Borders.THIN
        b.left = xlwt.Borders.THIN if left else xlwt.Borders.NO_LINE
        b.top_colour = b.bottom_colour = b.right_colour = 10
        b.left_colour = 10 if left else 8
        if head:
            st.font.colour_index = 9
            st.font._weight = 700  # 원본 FONT 레코드: weight 700, bold 비트 0(xlwt bold=True는 비트도 켠다)
            st.alignment.horz, st.alignment.vert, st.alignment.wrap = xlwt.Alignment.HORZ_CENTER, xlwt.Alignment.VERT_CENTER, 1
            st.pattern.pattern = xlwt.Pattern.SOLID_PATTERN
            st.pattern.pattern_fore_colour, st.pattern.pattern_back_colour = 8, 64
        return st
    return [style(True, i == 0) for i in range(len(EDUFINE))], [style(False, i == 0) for i in range(len(EDUFINE))]


def edufine_xls(c):
    """진짜 .xls(BIFF8). 시트 이름·머리글·서식·칸 너비는 원본 양식과 같게(테스트가 원본 파일과 비교한다)."""
    wb = xlwt.Workbook(encoding="utf-8")
    wb.set_colour_RGB(8, 89, 89, 91)  # 원본 팔레트(8 = 머리글 바탕, 10 = 테두리)
    wb.set_colour_RGB(10, 164, 164, 165)
    ws = wb.add_sheet("품목내역")
    head, body = _edufine_styles()
    for i, name in enumerate(EDUFINE):
        ws.col(i).width = 3600
        ws.write(0, i, name, head[i])
    for r, vals in enumerate(edufine_rows(c), start=1):
        for i, v in enumerate(vals):
            ws.write(r, i, _xls_num(v) if i >= 3 and v else v, body[i])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@never_cache
@require_GET
@owner_view(Collection)
def export_xls(request, c):
    """S1 에듀파인 엑셀(예산 품목 취합만). 권한은 CSV와 같다."""
    if c.source != Collection.Source.BUDGET:
        raise Http404
    r = HttpResponse(edufine_xls(c), content_type="application/vnd.ms-excel")
    name = BAD_NAME.sub("_", c.title).strip() or "취합"
    r["Content-Disposition"] = content_disposition_header(True, f"{name}_품목내역.xls")
    r["X-Content-Type-Options"] = "nosniff"
    return r


@never_cache
@require_GET
@owner_view(Collection)
def copy(request, c):
    """합본 표 복사(브라우저가 text/html·text/plain 둘 다 클립보드에 넣는다)."""
    lines = sheet(c)
    return JsonResponse({"html": to_html(lines), "tsv": to_tsv(lines)})


@never_cache
@require_POST
@owner_view(Collection)
def footer(request, c):
    """꼬리말 켜고 끄기(htmx, 동학년 회원 누구나, 학교 공간은 만든 사람·관리자). 체크 상자 name=on."""
    c.footer_on = request.POST.get("on") == "on"
    c.save(update_fields=["footer_on"])
    toast = "내보내는 파일에 꼬리말을 넣어요." if c.footer_on else "꼬리말 없이 내보내요."
    return render(request, "collect/_footer.html", {"c": c, "space": request.space, "footer_toast": toast})
