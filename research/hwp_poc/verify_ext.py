"""독립 구현으로 결과 파일 재검증: rhwp(Rust/WASM)로 열기·쪽수, pyhwp(별도 파서)로 HWP 출력 속 값 확인."""
import os, json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
W = os.environ.get('HWP_WORK') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = '/opt/homebrew/bin/node'
recs = [json.loads(l) for l in open(W + '/out/fill_results.jsonl')]
def pyhwp_opens(path):
    r = subprocess.run([W + '/venv39/bin/python', '-I', '-c', 'import sys;from hwp5.hwp5proc import main;sys.argv=["hwp5proc","xml",sys.argv[1]];main()', path], capture_output=True, timeout=180)
    return r.returncode == 0 and len(r.stdout) > 0
def raw_para_texts(path):
    """olefile + 레코드 직접 파싱(python-hwpx와 무관한 독립 구현)으로 문단 글자 수집"""
    import olefile, zlib, struct
    o = olefile.OleFileIO(path); fh = o.openstream('FileHeader').read(); comp = struct.unpack('<I', fh[36:40])[0] & 1
    out = []
    for s in [s for s in o.listdir() if s[0] == 'BodyText']:
        raw = o.openstream(s).read(); raw = zlib.decompress(raw, -15) if comp else raw
        i = 0
        while i + 4 <= len(raw):
            h = struct.unpack('<I', raw[i:i+4])[0]; i += 4; tag = h & 0x3FF; size = h >> 20
            if size == 0xFFF: size = struct.unpack('<I', raw[i:i+4])[0]; i += 4
            if tag == 67: out.append(raw[i:i+size].decode('utf-16le', 'replace').strip('\r\n\x00 '))
            i += size
    return out
def run(d):
    if d.get('status') != 'done': return None
    od = d['outdir']; src = f"{W}/samples/all/{d['file']}"
    files = [src, od + '/orig.hwpx', od + '/lib.hwpx'] + ([od + '/lib.hwp', od + '/roundtrip.hwp'] if d['kind'] == 'hwp' and d.get('hwp_write') == 'ok' else [])
    files = [f for f in files if os.path.exists(f)]
    res = {'file': d['file']}
    try:
        r = subprocess.run([NODE, os.path.dirname(os.path.abspath(__file__)) + '/rhwp_check.mjs', *files], capture_output=True, text=True, timeout=300)
        res['rhwp'] = {os.path.basename(x['f']) if '/out/' in x['f'] else 'source': x for x in map(json.loads, r.stdout.splitlines())}
        if r.returncode: res['rhwp_exit'] = r.returncode
    except subprocess.TimeoutExpired:
        res['rhwp'] = 'timeout'
    if d['kind'] == 'hwp' and os.path.exists(od + '/lib.hwp'):
        vals = list(json.load(open(od + '/values.json'))['values'].values())
        try:
            res['pyhwp_opens'] = pyhwp_opens(od + '/lib.hwp')
            s = set(raw_para_texts(od + '/lib.hwp'))
            res['raw_values_ok'] = sum(1 for v in vals if v in s); res['raw_values_total'] = len(vals)
        except Exception as e:
            res['pyhwp'] = 'error:' + str(e)[:80]
    return res
with ThreadPoolExecutor(6) as ex, open(W + '/out/ext_results.jsonl', 'w') as f:
    for x in ex.map(run, recs):
        if x: f.write(json.dumps(x, ensure_ascii=False) + '\n'); f.flush()
print('ok')
