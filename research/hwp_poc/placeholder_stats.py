"""모든 문서의 표 칸을 유형별로 센다: 빈 칸 / 단위만 / '라벨:' 꼴 / 체크박스 / 날짜 자리 / 서명 자리."""
import sys, os, glob, json, re, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import troad_hwp as T
W = os.environ.get('HWP_WORK') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = {'unit': re.compile(r'^\s*(명|개|원|천원|%|회|시간|건|일|학급|권)\s*$'),
     'colon': re.compile(r'[:：]\s*$'),
     'checkbox': re.compile(r'[□☐◻]'),
     'date': re.compile(r'년\s+월\s+일|\.\s+\.\s+\.|년\s{2,}월'),
     'sign': re.compile(r'\(\s*(인|서명|印)\s*\)|서명\s*$'),
     'paren_blank': re.compile(r'\(\s{2,}\)|\(\s*(남\s*/\s*여|한글|한자)\s*\)')}
docs = collections.Counter(); cells = collections.Counter()
files = sorted(glob.glob(W + '/samples/all/*'))
for f in files:
    try:
        hx, kind, _ = T.load(f); ts = T.analyze(hx)
    except Exception:
        continue
    seen = set()
    for t in ts:
        for c in t.cells:
            if c.has_nested: continue
            if not c.text and not c.has_obj: k = 'empty'
            else:
                k = next((n for n, rx in P.items() if rx.search(c.text)), None)
            if k: cells[k] += 1; seen.add(k)
    for k in seen: docs[k] += 1
    docs['_total'] += 1
print(json.dumps({'docs': docs, 'cells': cells}, ensure_ascii=False))
