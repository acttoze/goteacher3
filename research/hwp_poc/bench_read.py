import sys, os, json, subprocess, glob
from concurrent.futures import ThreadPoolExecutor
W = os.environ.get('HWP_WORK') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = W + '/venv/bin/python'; PY39 = W + '/venv39/bin/python'
files = sorted(glob.glob(W + '/samples/all/*'))
def run(p):
    mode = p.rsplit('.', 1)[-1]
    try:
        r = subprocess.run([PY, '-I', os.path.dirname(os.path.abspath(__file__)) + '/read_one.py', p, mode], capture_output=True, text=True, timeout=120)
        d = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"file": os.path.basename(p), "crash": r.stderr[-200:]}
    except subprocess.TimeoutExpired:
        d = {"file": os.path.basename(p), "timeout": True}
    d['mode'] = mode
    if mode == 'hwp':  # (c) pyhwp: hwp5proc xml 로 TableControl 개수
        try:
            r = subprocess.run([PY39, '-I', '-c', 'import sys,io;from hwp5.xmlmodel import Hwp5File;from hwp5.hwp5proc import main;sys.argv=["hwp5proc","xml",sys.argv[1]];main()', p], capture_output=True, timeout=120)
            x = r.stdout
            d['pyhwp_ok'] = r.returncode == 0 and len(x) > 0
            d['pyhwp_tables'] = x.count(b'<TableControl') if d['pyhwp_ok'] else None
            if not d['pyhwp_ok']: d['pyhwp_err'] = r.stderr.decode('utf-8', 'replace').strip().splitlines()[-1][:100] if r.stderr else 'empty'
        except subprocess.TimeoutExpired:
            d['pyhwp_ok'] = False; d['pyhwp_err'] = 'timeout'
    return d
with ThreadPoolExecutor(8) as ex, open(W + '/out/read_results.jsonl', 'w') as f:
    for i, d in enumerate(ex.map(run, files)):
        f.write(json.dumps(d, ensure_ascii=False) + '\n')
        if i % 100 == 0: print(i, flush=True)
