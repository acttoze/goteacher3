"""채운 결과(lib.hwpx)를 렌더해 '값'이 들어간 첫 쪽을 PNG로 뽑고 2x2 묶음 이미지로 만든다(사람 눈 판정용)."""
import sys, os, json, glob, subprocess, random
from PIL import Image, ImageDraw, ImageFont
W = os.environ.get('HWP_WORK') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
recs = [json.loads(l) for l in open(W + '/out/fill_results.jsonl')]
done = [r for r in recs if r['status'] == 'done' and r['set'] == 'keyword']
random.seed(7); pick = random.sample(done, min(int(sys.argv[1]) if len(sys.argv) > 1 else 16, len(done)))
os.makedirs(W + '/out/review', exist_ok=True)
imgs = []
for n, r in enumerate(pick):
    od = r['outdir']; rd = od + '/render'
    subprocess.run(['/opt/homebrew/bin/node', os.path.dirname(os.path.abspath(__file__)) + '/render.mjs', od + '/lib.hwpx', rd], capture_output=True, timeout=120)
    svgs = sorted(glob.glob(rd + '/p*.svg'), key=lambda p: int(os.path.basename(p)[1:-4]))
    page = next((s for s in svgs if '값' in open(s, encoding='utf-8', errors='ignore').read()), svgs[0] if svgs else None)
    if not page: continue
    png = f"{W}/out/review/{n:02d}.png"
    subprocess.run(['sh', os.path.dirname(os.path.abspath(__file__)) + '/svg2png.sh', page, png], capture_output=True, timeout=60)
    imgs.append((n, r, png))
    print(n, r['title'][:50], r['pick']['rows'], 'x', r['pick']['cols'], r['pick']['kind'], 'inputs', r['pick']['inputs'], 'cands', r['n_candidates'], os.path.basename(page))
for g in range(0, len(imgs), 4):
    sheet = Image.new('RGB', (1640, 2320), 'white')
    for k, (n, r, png) in enumerate(imgs[g:g + 4]):
        im = Image.open(png).convert('RGB').resize((820, 1160))
        ImageDraw.Draw(im).rectangle([0, 0, 60, 40], fill='yellow'); ImageDraw.Draw(im).text((10, 10), f'#{n}', fill='black')
        sheet.paste(im, ((k % 2) * 820, (k // 2) * 1160))
    sheet.save(f'{W}/out/review/sheet{g // 4}.png')
