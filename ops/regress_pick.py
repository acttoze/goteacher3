"""F4 회귀 표본 선정(로컬에서 한 번). 아카이브 raw/ 아래 hwp·hwpx에서 100개를 고정 seed로 고른다.

    python3 -I ops/regress_pick.py <아카이브 raw 폴더> > <목록.txt>

출력은 파일 목록(raw 기준 상대 경로, 한 줄에 하나)뿐이다. 문서 내용은 읽지 않는다(크기만 본다).
고르는 법: hwp 70 + hwpx 30(hwp 변환·HWP 다시 쓰기 경로를 더 많이), 종류마다 크기 순으로 4등분해
칸마다 같은 수를 뽑는다(작은 신청서~큰 계획서가 고루 들어가게). seed 1030 고정 → 같은 아카이브면 같은 목록.
표본을 바꾸면 기준값(var/regress-baseline.json)도 지우고 새로 잡는다(ops/README.md).
"""
import os
import random
import sys

SEED = 1030
WANT = {'hwp': 70, 'hwpx': 30}
BINS = 4


def main(root):
    pool = {k: [] for k in WANT}
    for d, _, fs in os.walk(root):
        for f in fs:
            k = f.rsplit('.', 1)[-1].lower()
            if k in pool:
                p = os.path.join(d, f)
                pool[k].append((os.path.getsize(p), os.path.relpath(p, root)))
    rnd = random.Random(SEED)
    out = []
    for k, n in WANT.items():
        xs = sorted(pool[k])
        step = len(xs) / BINS
        for b in range(BINS):
            part = [r for _, r in xs[int(b * step):int((b + 1) * step)]]
            out += rnd.sample(part, n // BINS + (1 if b < n % BINS else 0))
    names = [os.path.basename(r) for r in out]
    assert len(set(names)) == len(names), '파일 이름 겹침 — 서버에는 한 폴더에 둔다'
    print('\n'.join(sorted(out)))


if __name__ == '__main__':
    main(sys.argv[1])
