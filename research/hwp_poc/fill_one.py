"""파일 하나: 표 고르기 → 칸 매핑 → 채우기(lib/own) → 검증 → (hwp면) HWP 다시 쓰기. JSON 한 줄 출력."""
import sys, os, json, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import troad_hwp as T
p, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)
r = {'file': os.path.basename(p)}
try:
    t0 = time.time()
    hx, kind, rep = T.load(p); r['kind'] = kind; r['conv_report'] = rep
    tables = T.analyze(hx)
    r['n_tables'] = len(tables)
    r['roles'] = {k: sum(1 for t in tables if t.role == k) for k in ('data', 'approval', 'layout', 'frame', 'filled')}
    cands = T.pick_table(tables)
    r['n_candidates'] = len(cands)
    if not cands:
        r['status'] = 'no_candidate'; print(json.dumps(r, ensure_ascii=False)); sys.exit()
    best = cands[0]; s = T.schema(best)
    r['pick'] = {'section': best.section, 'index': best.index, 'rows': best.rows, 'cols': best.cols, 'nested': best.nested,
                 'merged': sum(1 for c in best.cells if c.rs > 1 or c.cs > 1), 'before': best.before_text[:40],
                 'kind': s['kind'], 'header_rows': s['header_rows'], 'label_cols': s['label_cols'], 'inputs': len(s['inputs']),
                 'total_rows': s['total_rows'], 'labels_sample': [i['label'] for i in s['inputs'][:4]]}
    vals = {(i['row'], i['col']): f"값{n+1}{i['unit']}" for n, i in enumerate(s['inputs'])}
    open(f'{outdir}/orig.hwpx', 'wb').write(hx)
    t1 = time.time()
    a = T.fill_lib(hx, best.section, best.index, vals); r['lib_ms'] = int((time.time() - t1) * 1000)
    open(f'{outdir}/lib.hwpx', 'wb').write(a)
    r['lib'] = T.verify(hx, a, best.section, best.index, vals)
    try:
        b = T.fill_own(hx, best.section, best.index, vals)
        r['own'] = T.verify(hx, b, best.section, best.index, vals)
    except Exception as e:
        r['own'] = {'error': type(e).__name__ + ':' + str(e)[:100]}
    if kind == 'hwp':
        # HWP 그대로 돌려주기: 채운 HWPX → HWP 5.0
        try:
            t1 = time.time(); hb = T.to_hwp(a); r['hwp_write_ms'] = int((time.time() - t1) * 1000)
            open(f'{outdir}/lib.hwp', 'wb').write(hb)
            # 다시 읽기(python-hwpx)로 값 확인
            hx2, _, _ = T.load(f'{outdir}/lib.hwp')
            ta = [t for t in T.analyze(hx2) if t.section == best.section]
            amap = {(c.row, c.col): c.text for c in ta[best.index].cells}
            r['hwp_reread_values_ok'] = sum(1 for k, v in vals.items() if amap.get(k) == v)
            r['hwp_write'] = 'ok'
        except Exception as e:
            r['hwp_write'] = type(e).__name__ + ':' + str(e)[:120]
        # 채우지 않은 원본 왕복(HWP→HWPX→HWP)도 되는지
        try:
            open(f'{outdir}/roundtrip.hwp', 'wb').write(T.to_hwp(hx)); r['hwp_roundtrip'] = 'ok'
        except Exception as e:
            r['hwp_roundtrip'] = type(e).__name__ + ':' + str(e)[:120]
    json.dump({'values': {f'{k[0]},{k[1]}': v for k, v in vals.items()}, 'section': best.section, 'index': best.index}, open(f'{outdir}/values.json', 'w'), ensure_ascii=False)
    r['status'] = 'done'; r['total_ms'] = int((time.time() - t0) * 1000)
except Exception as e:
    r['status'] = 'error'; r['error'] = type(e).__name__ + ':' + str(e)[:150]; r['tb'] = traceback.format_exc()[-300:]
print(json.dumps(r, ensure_ascii=False))
