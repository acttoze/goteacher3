// 사용법: node render.mjs <입력.hwp|hwpx> <출력폴더>  — rhwp(WASM)로 페이지별 SVG 생성
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
const here = path.dirname(new URL(import.meta.url).pathname);
const { existsSync } = await import('node:fs');
const modDir = process.env.RHWP_DIR || [path.join(here, 'node_modules/@rhwp/core'), path.join(here, '../npm/node_modules/@rhwp/core')].find(existsSync);
const { initSync, HwpDocument } = await import(path.join(modDir, 'rhwp.js'));
// 글자 폭 측정: 브라우저 canvas 대신 근사치(한글·전각 1em, 그 외 0.55em)
globalThis.measureTextWidth = (font, text) => {
  const m = /(\d+(?:\.\d+)?)px/.exec(font); const px = m ? parseFloat(m[1]) : 13;
  let w = 0; for (const ch of text) w += (ch.charCodeAt(0) > 0x2e80 ? 1.0 : 0.55) * px; return w;
};
initSync({ module: readFileSync(path.join(modDir, 'rhwp_bg.wasm')) });
const [,, inp, outDir] = process.argv;
mkdirSync(outDir, { recursive: true });
const doc = new HwpDocument(new Uint8Array(readFileSync(inp)));
const n = doc.pageCount();
for (let i = 0; i < n; i++) writeFileSync(path.join(outDir, `p${i + 1}.svg`), doc.renderPageSvg(i));
console.log(JSON.stringify({ pages: n }));
