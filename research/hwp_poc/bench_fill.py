import os, sys, json, glob, subprocess, csv
from concurrent.futures import ThreadPoolExecutor
W = os.environ.get('HWP_WORK') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = '/Users/o0o0o/moon/marketing/goteacher-forms-archive'
title = {r['bf_file']: r['bf_source'] for r in csv.DictReader(open(A + '/files.tsv', encoding='utf-8'), delimiter='\t')}
kw = [l.split('\t')[3].strip().split('/')[-1] for l in open(W + '/cands.txt', encoding='utf-8').read().splitlines()[1:]]
allx = [os.path.basename(p) for p in glob.glob(W + '/samples/all/*.hwpx')]
names = list(dict.fromkeys(kw + allx))
def run(n):
    import hashlib; od = f'{W}/out/fill/' + hashlib.md5(n.encode()).hexdigest()[:12]
    try:
        r = subprocess.run([W + '/venv/bin/python', '-I', os.path.dirname(os.path.abspath(__file__)) + '/fill_one.py', f'{W}/samples/all/{n}', od], capture_output=True, text=True, timeout=300)
        d = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {'file': n, 'status': 'crash', 'error': r.stderr[-200:]}
    except subprocess.TimeoutExpired:
        d = {'file': n, 'status': 'timeout'}
    d['title'] = title.get(n, ''); d['set'] = 'keyword' if n in kw else 'hwpx_all'; d['outdir'] = od
    return d
with ThreadPoolExecutor(8) as ex, open(W + '/out/fill_results.jsonl', 'w') as f:
    for i, d in enumerate(ex.map(run, names)):
        f.write(json.dumps(d, ensure_ascii=False) + '\n'); f.flush()
print('done', len(names))
