"""V7 캡처용 로컬 서버: runserver(--noreload)에 가짜 급식 메뉴를 붙여 띄운다. capture.py가 부른다.

가상 학교 코드는 NEIS에 없으므로 spaces.neis.meals·schedule을 이 프로세스에서만 바꿔 끼운다(앱 코드·설정은 그대로).
    cd app && ../.venv/bin/python ../ops/help/serve.py 8765
"""
import os
import sys
from datetime import date, timedelta

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app")
sys.path.insert(0, APP)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from spaces import neis  # noqa: E402

MENUS = [
    ["현미밥", "쇠고기미역국(5.6.16)", "닭갈비(5.6.13.15)", "깍두기(9)", "사과"],
    ["차조밥", "된장찌개(5.6)", "고등어구이(7)", "시금치나물(5.6)", "배추김치(9)"],
    ["카레라이스(2.5.6.10)", "유부장국(5.6)", "치킨너겟(1.2.5.6.15)", "배추김치(9)", "요구르트(2)"],
    ["잡곡밥", "순두부찌개(5.6.9)", "불고기(5.6.16)", "콩나물무침(5)", "귤"],
    ["볶음밥(1.5.6)", "어묵국(1.5.6)", "탕수육(1.2.5.6.10)", "단무지", "우유(2)"],
]


def fake_meals(atpt, code, start, end):
    d, last, out = date(int(start[:4]), int(start[4:6]), int(start[6:])), date(int(end[:4]), int(end[4:6]), int(end[6:])), []
    while d <= last:
        if d.weekday() < 5:
            dishes = [neis._dish(x) for x in MENUS[d.weekday()]]
            out.append({"ymd": f"{d:%Y%m%d}", "dishes": dishes, "kcal": 680 + 10 * d.weekday(), "count": 412,
                        "nutr": [{"name": "탄수화물", "unit": "g", "value": 95.2}, {"name": "단백질", "unit": "g", "value": 28.4}],
                        "origin": [["쌀", "국내산"], ["쇠고기", "국내산(한우)"]]})
        d += timedelta(days=1)
    return out


neis.meals = fake_meals
neis.schedule = lambda *a, **k: []
neis.day_off = lambda *a, **k: False
call_command("runserver", sys.argv[1] if len(sys.argv) > 1 else "8765", use_reloader=False)
