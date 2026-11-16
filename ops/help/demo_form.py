"""V7 캡처용 가상 한글 양식(.hwpx) 하나를 만든다: 결재란 + 반별 명단 표. capture.py가 앱 가상환경 파이썬으로 부른다.
    .venv/bin/python ops/help/demo_form.py /tmp/교실_환경_점검표.hwpx
"""
import sys

from hwpx.document import HwpxDocument

ROWS = ["1-1", "2-1", "3-1", "4-1", "4-2", "5-1", "6-1"]

d = HwpxDocument.new()
d.add_paragraph("결재")
a = d.add_table(2, 3)
for c, x in enumerate(["담당", "교무부장", "교장"]):
    a.set_cell_text(0, c, x)
d.add_paragraph("교실 환경 점검표")
t = d.add_table(len(ROWS) + 1, 4)
for c, x in enumerate(["학년반", "담임", "노트북(대)", "점검 필요한 곳"]):
    t.set_cell_text(0, c, x)
for r, cls in enumerate(ROWS, 1):
    t.set_cell_text(r, 0, cls)
with open(sys.argv[1], "wb") as f:
    f.write(d.to_bytes())
