"""hwp 패키지 회귀 벤치. research/hwp_poc 의 bench_read/bench_fill 과 같은 기준을 공개 API로 잰다.

    uv run python -m hwp.bench read <표본폴더>                      # 읽기: 판별·변환·표 분석
    uv run python -m hwp.bench fill <표본폴더> [--names 목록.txt]    # 1순위 표 채우기 → 검증 → (hwp면) HWP 다시 쓰기

표본은 신뢰하지 않는 입력이다. 저장소 밖 복사본 폴더를 넘긴다. 결과에 문서 내용은 남기지 않는다.
--names: 채우기 대상 파일 이름 목록(한 줄에 하나). 없으면 폴더의 전부.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import hwp
from hwp import engine as E


def read_one(p: str) -> dict:
    r = {'file': os.path.basename(p)}
    t0 = time.time()
    try:
        data = open(p, 'rb').read()
        hx, kind, rep = hwp.to_hwpx(data)
        r.update(kind=kind, ok=True, tables=len(E.analyze(hx)), lossy=bool(rep.get('unconverted') or rep.get('dropped')))
    except hwp.HwpError as e:
        r.update(ok=False, error=type(e).__name__)
    except Exception as e:
        r.update(ok=False, error='crash:' + type(e).__name__)
    r['ms'] = int((time.time() - t0) * 1000)
    return r


def fill_one(p: str) -> dict:
    r = {'file': os.path.basename(p)}
    t0 = time.time()
    try:
        hx, kind, _ = hwp.to_hwpx(open(p, 'rb').read())
        r['kind'] = kind
        cands = hwp.candidates(hx)
        if not cands:
            r['status'] = 'no_candidate'
            return r
        best = cands[0]
        s = hwp.schema(hx, best['section'], best['index'])
        vals = {(i['row'], i['col']): f"값{n + 1}{i['unit']}" for n, i in enumerate(s['inputs'])}
        r['inputs'] = len(vals)
        t1 = time.time()
        out = hwp.fill(hx, best['section'], best['index'], vals)
        r['fill_ms'] = int((time.time() - t1) * 1000)
        r['hwpx'] = 'ok'
        if kind == 'hwp':
            t1 = time.time()
            hwp.to_hwp(out, best['section'], best['index'], vals)
            r['hwp_ms'] = int((time.time() - t1) * 1000)
            r['hwp'] = 'ok'
            hwp.to_hwp(hx, best['section'], best['index'], {})   # 채우지 않은 왕복
            r['roundtrip'] = 'ok'
        r['status'] = 'done'
    except hwp.FillError as e:
        r.update(status='fail', error=str(e), problems=[[list(a), why] for a, why in e.problems[:5]])
    except Exception as e:
        r.update(status='error', error=type(e).__name__)
    r['total_ms'] = int((time.time() - t0) * 1000)
    return r


def _pct(a, b):
    return f'{a}/{b} ({100 * a / max(b, 1):.1f}%)'


def _ms(xs):
    xs = sorted(xs)
    return f'중앙값 {xs[len(xs) // 2]} / 95% {xs[int(len(xs) * .95)]} / 최대 {xs[-1]}' if xs else '-'


def summarize_read(R):
    for kind in ('hwpx', 'hwp'):
        X = [r for r in R if r.get('kind') == kind]
        print(f'== 읽기 {kind}: {len(X)}개, 표 1개 이상 {_pct(sum(r["tables"] > 0 for r in X), len(X))}, '
              f'변환 손실 보고 {sum(r["lossy"] for r in X)}, ms {_ms([r["ms"] for r in X])}')
    bad = [r for r in R if not r['ok']]
    print(f'== 읽기 실패 {len(bad)}', sorted({r["error"] for r in bad}))


def summarize_fill(R):
    for kind in ('hwp', 'hwpx'):
        X = [r for r in R if r.get('kind') == kind]
        D = [r for r in X if r['status'] in ('done', 'fail', 'error')]
        print(f'== 채우기 {kind}: {len(X)}개, 후보 있음 {len(D)}, 후보 없음 {sum(r["status"] == "no_candidate" for r in X)}')
        print(f'  hwpx 채우기+검증 통과 {_pct(sum(r.get("hwpx") == "ok" for r in D), len(D))}, '
              f'채운 칸 {sum(r.get("inputs", 0) for r in D)}, fill ms {_ms([r["fill_ms"] for r in D if "fill_ms" in r])}')
        if kind == 'hwp':
            print(f'  HWP 다시 쓰기+재읽기 값 일치 {_pct(sum(r.get("hwp") == "ok" for r in D), len(D))}, '
                  f'무변경 왕복 {_pct(sum(r.get("roundtrip") == "ok" for r in D), len(D))}, hwp ms {_ms([r["hwp_ms"] for r in D if "hwp_ms" in r])}')
        print(f'  문서당 전체 ms {_ms([r["total_ms"] for r in D])}')
    bad = [r for r in R if r.get('status') in ('fail', 'error') or (r.get('status') is None)]
    for r in bad[:10]:
        print('  실패', r['file'][:24], r.get('error'), r.get('problems'))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['read', 'fill'])
    ap.add_argument('dir')
    ap.add_argument('--names')
    ap.add_argument('--out')
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args(argv)
    if a.names:
        names = list(dict.fromkeys(x.strip() for x in open(a.names, encoding='utf-8') if x.strip()))
    else:
        names = sorted(os.listdir(a.dir))
    paths = [os.path.join(a.dir, n) for n in names if n.lower().endswith(('.hwp', '.hwpx'))]
    fn = read_one if a.mode == 'read' else fill_one
    with ProcessPoolExecutor(a.jobs) as ex:
        R = list(ex.map(fn, paths, chunksize=4))
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            for r in R:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
    (summarize_read if a.mode == 'read' else summarize_fill)(R)
    return 0


if __name__ == '__main__':
    sys.exit(main())
