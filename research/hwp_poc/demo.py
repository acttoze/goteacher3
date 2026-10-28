"""티로드 한글 취합 종단 데모.

  python demo.py <양식.hwp|hwpx> <출력폴더> [--table N] [--people 3] [--add-rows K]

1) 문서 안 표를 모두 분석해 후보 순위를 보여 주고(사람이 확인할 지점)
2) 고른 표의 입력 칸 목록(schema.json = 웹 입력 칸)을 만들고
3) 여러 명이 각자 자기 몫(행 머리 묶음 또는 행)만 채워 제출한 것을 흉내 내어
4) 서버에서 병합(같은 칸 중복 제출은 충돌로 보고)한 뒤
5) 원본 파일 그 표에 값을 넣어 저장한다. hwp 원본이면 .hwp 와 .hwpx 둘 다 만든다.
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import troad_hwp as T

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('out')
ap.add_argument('--table', type=int, help='후보 순위 번호(0부터). 생략하면 1순위')
ap.add_argument('--people', type=int, default=3)
ap.add_argument('--add-rows', type=int, default=0, help='명단형: 마지막 행을 복제해 K행 추가')
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

hx, kind, rep = T.load(a.src)
tables = T.analyze(hx)
print(f'[1] {kind} 문서, 표 {len(tables)}개 (자료표 {sum(t.role == "data" for t in tables)}, 결재란 {sum(t.role == "approval" for t in tables)}, 틀 {sum(t.role == "frame" for t in tables)}, 상자 {sum(t.role == "layout" for t in tables)})')
cands = T.pick_table(tables)
for n, t in enumerate(cands[:5]):
    print(f'    후보{n}: 섹션{t.section[-5]} 표#{t.index} {t.rows}x{t.cols} 점수{t.score:.0f} 앞글:{t.before_text[:20]!r} 첫행:{[c.text for c in t.cells if c.row == 0][:5]}')
if not cands:
    sys.exit('취합용 표 후보 없음 — 사람이 직접 지정 필요')
best = cands[a.table or 0]

if a.add_rows:
    from hwpx.table_patch import apply_table_ops
    r = apply_table_ops(hx, [{'op': 'insert_row_by_clone', 'section_path': best.section, 'table_index': best.index,
                              'ref_row': best.rows - 1, 'count': a.add_rows, 'blank': True}])
    hx = r.data
    best = [t for t in T.analyze(hx) if t.section == best.section][best.index]
    print(f'    행 {a.add_rows}개 추가 → {best.rows}행')

s = T.schema(best)
json.dump({'section': best.section, 'table_index': best.index, **s}, open(f'{a.out}/schema.json', 'w'), ensure_ascii=False, indent=1)
print(f'[2] 입력 칸 {len(s["inputs"])}개 ({s["kind"]}형, 머리글 {s["header_rows"]}행, 행머리 {s["label_cols"]}열, 합계행 {s["total_rows"]}) → schema.json')
for i in s['inputs'][:5]:
    print('    ', i['label'], (i['row'], i['col']))

# 3) 제출 흉내: 행 단위로 사람들에게 나눠 준다(실서비스에선 '내 반' 행만 열어 줌)
rows = sorted({i['row'] for i in s['inputs']})
subs = []
for p in range(a.people):
    mine = [i for i in s['inputs'] if rows.index(i['row']) % a.people == p]
    subs.append({'who': f'교사{p + 1}', 'cells': {f"{i['row']},{i['col']}": f"교사{p + 1}-{n + 1}{i['unit']}" for n, i in enumerate(mine)}})
subs.append({'who': '교사1(재제출)', 'cells': dict(list(subs[0]['cells'].items())[:1])})  # 충돌 사례
json.dump(subs, open(f'{a.out}/submissions.json', 'w'), ensure_ascii=False, indent=1)

# 4) 병합: 나중 제출이 이긴다, 다른 사람 칸을 덮으면 충돌로 기록
merged, owner, conflicts = {}, {}, []
for sub in subs:
    who = sub['who'].split('(')[0]
    for k, v in sub['cells'].items():
        if k in owner and owner[k] != who:
            conflicts.append((k, owner[k], who))
        merged[k] = v; owner[k] = who
print(f'[3-4] 제출 {len(subs)}건 병합 → {len(merged)}칸, 다른 사람 칸 덮어쓰기 충돌 {len(conflicts)}건')

# 5) 원본 표에 넣고 저장
vals = {tuple(map(int, k.split(','))): v for k, v in merged.items()}
out = T.fill_lib(hx, best.section, best.index, vals)
v = T.verify(hx, out, best.section, best.index, vals)
base = os.path.splitext(os.path.basename(a.src))[0][:20]
open(f'{a.out}/result.hwpx', 'wb').write(out)
print(f'[5] result.hwpx 저장: 값 {v["values_ok"]}/{v["values_total"]}, 구조동일 {v["structure_same"]}, 다른칸변경 {v["other_cells_changed"]}, 바뀐파트 {v["parts_changed"]}, zip오류 {v["zip_errors"]}')
if kind == 'hwp':
    hb = T.to_hwp(out)
    open(f'{a.out}/result.hwp', 'wb').write(hb)
    back, _, _ = T.load(f'{a.out}/result.hwp')
    t2 = [t for t in T.analyze(back) if t.section == best.section][best.index]
    m = {(c.row, c.col): c.text for c in t2.cells}
    print(f'    result.hwp 저장(HWP 5.0): 다시 읽어 값 {sum(1 for k, x in vals.items() if m.get(k) == x)}/{len(vals)} 일치')
