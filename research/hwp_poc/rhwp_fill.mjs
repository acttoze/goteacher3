// 대안 경로: rhwp(WASM)로 HWP/HWPX 원본을 직접 열어 표의 빈 칸에 글자를 넣고 같은 형식으로 저장
// 사용법: node rhwp_fill.mjs <입력> <출력> [표 순번(본문 최상위 표 기준, 기본: 빈 칸 가장 많은 표)]
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
const here = path.dirname(new URL(import.meta.url).pathname);
const { existsSync } = await import('node:fs');
const modDir = process.env.RHWP_DIR || [path.join(here, 'node_modules/@rhwp/core'), path.join(here, '../npm/node_modules/@rhwp/core')].find(existsSync);
const { initSync, HwpDocument } = await import(path.join(modDir, 'rhwp.js'));
globalThis.measureTextWidth = (font, text) => { const m = /(\d+(?:\.\d+)?)px/.exec(font); const px = m ? +m[1] : 13; let w = 0; for (const ch of text) w += (ch.charCodeAt(0) > 0x2e80 ? 1 : 0.55) * px; return w; };
initSync({ module: readFileSync(path.join(modDir, 'rhwp_bg.wasm')) });
const [,, inp, outp, pickArg] = process.argv;
const doc = new HwpDocument(new Uint8Array(readFileSync(inp)));
const tables = [];
const nPara = doc.getParagraphCount(0);
for (let p = 0; p < nPara; p++) for (let c = 0; c < 8; c++) {
  try {
    const dim = JSON.parse(doc.getTableDimensions(0, p, c));
    const cells = JSON.parse(doc.getTableCellBboxes(0, p, c));
    const empty = cells.filter(x => doc.getCellParagraphLength(0, p, c, x.cellIdx, 0) === 0 && doc.getCellParagraphCount(0, p, c, x.cellIdx) === 1);
    tables.push({ p, c, dim, cells, empty });
  } catch (e) { /* 표가 아닌 컨트롤 */ }
}
const t = pickArg !== undefined ? tables[+pickArg] : tables.slice().sort((a, b) => b.empty.length - a.empty.length)[0];
let n = 0;
for (const x of t.empty) { doc.insertTextInCell(0, t.p, t.c, x.cellIdx, 0, 0, `값R${x.row}C${x.col}`); n++; }
const out = outp.endsWith('.hwpx') ? doc.exportHwpx() : doc.exportHwp();
writeFileSync(outp, out);
console.log(JSON.stringify({ tables: tables.length, picked: { para: t.p, ctrl: t.c, ...t.dim }, filled: n, bytes: out.length }));
