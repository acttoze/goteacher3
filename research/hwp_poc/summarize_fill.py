import json, sys, collections
R = [json.loads(l) for l in open(sys.argv[1])]
pct = lambda a, b: f"{a}/{b} ({100*a/max(b,1):.1f}%)"
for name, S in [('전체', R), ('키워드 취합형', [r for r in R if r['set'] == 'keyword']), ('HWPX 전체', [r for r in R if r['set'] == 'hwpx_all'])]:
    for kind in ('hwp', 'hwpx'):
        X = [r for r in S if r.get('kind') == kind]
        if not X: continue
        D = [r for r in X if r['status'] == 'done']
        print(f'== {name} / {kind}: {len(X)}개, status', dict(collections.Counter(r["status"] for r in X)))
        if not D: continue
        def perfect(v): return isinstance(v, dict) and 'values_ok' in v and v['values_ok'] == v['values_total'] and v['structure_same'] and not v['zip_errors'] and v['other_cells_changed'] == 0 and v['other_tables_changed'] == 0
        print('  lib 완벽', pct(sum(perfect(r['lib']) for r in D), len(D)), '| own 완벽', pct(sum(perfect(r['own']) for r in D), len(D)))
        print('  lib 바뀐 파트가 섹션 1개뿐', pct(sum(1 for r in D if len(r['lib']['parts_changed']) == 1 and not r['lib']['parts_added_removed']), len(D)))
        if kind == 'hwp':
            print('  HWP 다시쓰기', pct(sum(r.get('hwp_write') == 'ok' for r in D), len(D)), '| 재읽기 값 일치', pct(sum(1 for r in D if r.get('hwp_reread_values_ok') == r['pick']['inputs']), len(D)), '| 무변경 왕복', pct(sum(r.get('hwp_roundtrip') == 'ok' for r in D), len(D)))
            print('  HWP 쓰기 실패', collections.Counter(r.get('hwp_write')[:90] for r in D if r.get('hwp_write') != 'ok').most_common(5))
        print('  선택 표: list형', sum(r['pick']['kind'] == 'list' for r in D), 'kv형', sum(r['pick']['kind'] == 'kv' for r in D), '| 병합 있음', sum(r['pick']['merged'] > 0 for r in D), '| 중첩표가 선택', sum(r['pick']['nested'] for r in D))
        print('  후보 2개 이상(사람이 골라야 할 수 있음)', pct(sum(r['n_candidates'] >= 2 for r in D), len(D)), '| 결재란 검출 문서', sum(r['roles']['approval'] > 0 for r in D))
        ms = sorted(r['lib_ms'] for r in D); print('  채우기 ms 중앙값', ms[len(ms)//2], 'max', ms[-1])
        errs = collections.Counter((r.get('error') or '')[:80] for r in X if r['status'] == 'error'); 
        if errs: print('  error', errs.most_common(5))
        bad = [r for r in D if not perfect(r['lib'])]
        for b in bad[:5]: print('  lib불완전', b['title'][:30], {k: b['lib'].get(k) for k in ('values_ok', 'values_total', 'structure_same', 'other_cells_changed', 'zip_errors')})
        bad = [r for r in D if not perfect(r['own'])]
        for b in bad[:3]: print('  own불완전', b['title'][:30], b['own'] if 'error' in b['own'] else {k: b['own'].get(k) for k in ('values_ok', 'values_total', 'structure_same', 'other_cells_changed', 'zip_errors')})
