import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1])]
X = [r for r in rows if r['mode'] == 'hwpx']; H = [r for r in rows if r['mode'] == 'hwp']
pct = lambda a, b: f"{a}/{b} ({100*a/b:.1f}%)"
print('== HWPX', len(X))
print('own lxml ok', pct(sum(r.get('own_ok', False) for r in X), len(X)))
print('python-hwpx table_summary ok', pct(sum(r.get('phx_ok', False) for r in X), len(X)))
print('표 수 일치(own vs phx)', pct(sum(1 for r in X if r.get('own_ok') and r.get('phx_ok') and r['own']['tables'] == r['phx_tables']), len(X)))
ok = [r for r in X if r.get('own_ok')]
print('표 1개 이상', pct(sum(r['own']['tables'] > 0 for r in ok), len(ok)), '| 3x2 이상 표 있음', pct(sum(r['own']['big'] > 0 for r in ok), len(ok)),
      '| 병합 표 있음', pct(sum(r['own']['merged_tables'] > 0 for r in ok), len(ok)), '| 중첩 표 있음', pct(sum(r['own']['nested'] > 0 for r in ok), len(ok)),
      '| 표 2개 이상', pct(sum(r['own']['tables'] >= 2 for r in ok), len(ok)))
print('fail', collections.Counter((r.get('own_err') or r.get('phx_err') or '')[:60] for r in X if not (r.get('own_ok') and r.get('phx_ok'))))
print('== HWP', len(H))
print('timeout/crash', sum(1 for r in H if r.get('timeout') or r.get('crash')))
print('olefile 레코드 직접 ok', pct(sum(r.get('raw_ok', False) for r in H), len(H)))
print('암호', sum(r.get('password', False) for r in H), '배포용', sum(r.get('distribution', False) for r in H))
print('버전', collections.Counter(r.get('ver', '?')[:3] for r in H).most_common(6))
print('python-hwpx convert ok', pct(sum(r.get('phx_ok', False) for r in H), len(H)))
print('pyhwp ok', pct(sum(r.get('pyhwp_ok', False) for r in H), len(H)))
both = [r for r in H if r.get('raw_ok') and r.get('phx_ok')]
print('표 수 일치 raw vs phx', pct(sum(1 for r in both if r['raw_tables'] == r['phx']['tables']), len(both)))
b3 = [r for r in both if r.get('pyhwp_ok')]
print('표 수 일치 raw vs pyhwp', pct(sum(1 for r in b3 if r['raw_tables'] == r['pyhwp_tables']), len(b3)))
ok = [r for r in H if r.get('phx_ok')]
print('표 1개 이상', pct(sum(r['phx']['tables'] > 0 for r in ok), len(ok)), '| 3x2 이상', pct(sum(r['phx']['big'] > 0 for r in ok), len(ok)),
      '| 병합', pct(sum(r['phx']['merged_tables'] > 0 for r in ok), len(ok)), '| 중첩', pct(sum(r['phx']['nested'] > 0 for r in ok), len(ok)),
      '| 표 2개 이상', pct(sum(r['phx']['tables'] >= 2 for r in ok), len(ok)))
print('phx 변환 손실 보고 있는 파일', sum(1 for r in ok if r['phx_unconverted'] or r['phx_dropped']), collections.Counter(x for r in ok for x in r['phx_unconverted'] + r['phx_dropped']).most_common(8))
ms = sorted(r['phx_ms'] for r in ok); print('변환 시간 ms 중앙값', ms[len(ms)//2], '95%', ms[int(len(ms)*.95)], 'max', ms[-1])
print('phx fail', collections.Counter((r.get('phx_err') or '')[:70] for r in H if not r.get('phx_ok')).most_common(8))
print('pyhwp fail', collections.Counter((r.get('pyhwp_err') or '')[:70] for r in H if not r.get('pyhwp_ok')).most_common(5))
print('raw fail', collections.Counter((r.get('raw_err') or '')[:70] for r in H if not r.get('raw_ok')).most_common(5))
mm = [r for r in both if r['raw_tables'] != r['phx']['tables']]
print('불일치 예', [(r['raw_tables'], r['phx']['tables'], r.get('pyhwp_tables')) for r in mm[:10]])
